
import streamlit as st
import pandas as pd
import io
import unicodedata
from typing import List, Tuple
import chardet
import re

st.set_page_config(page_title="Pix CSV Uploader (BS2) – Consolidador", page_icon="💳", layout="wide")

# ---------- Utils ----------
def detect_encoding(b: bytes) -> str:
    if b.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    guess = chardet.detect(b)
    enc = (guess.get("encoding") or "utf-8").lower()
    if enc in ("iso-8859-1", "latin-1", "latin1"):
        return "latin1"
    return enc

def normalize_text(s: str) -> str:
    if s is None:
        return ""
    s = str(s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s.lower().strip()

def clean_val(series: pd.Series) -> pd.Series:
    s = series.astype(str)
    s = s.str.replace("\u00A0", "", regex=False)  # NBSP
    s = s.str.replace("R$", "", regex=False)
    s = s.str.replace(" ", "", regex=False)
    s = s.str.replace("\u2212", "-", regex=False)  # unicode minus
    s = s.str.replace(r"^([0-9\.,]+)-$", r"-\1", regex=True)  # 0,45- -> -0,45
    s = s.str.replace(".", "", regex=False)  # thousands
    s = s.str.replace(",", ".", regex=False)  # decimal
    return pd.to_numeric(s, errors="coerce")

def read_bs2_csv(file_bytes: bytes) -> pd.DataFrame:
    enc = detect_encoding(file_bytes)
    text = file_bytes.decode(enc, errors="replace")
    lines = text.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("data;"):
            header_idx = i
            break
    if header_idx is None:
        return pd.DataFrame()
    sliced = "\n".join(lines[header_idx:])
    from io import StringIO
    df = pd.read_csv(StringIO(sliced), sep=";", dtype=str, keep_default_na=False)
    df = df.rename(columns={c: c.strip() for c in df.columns})
    rename_map = {}
    for col in df.columns:
        c = col.strip().lower()
        if c.startswith("data"):
            rename_map[col] = "Data"
        elif c.startswith("tipo"):
            rename_map[col] = "Tipo"
        elif c.startswith("detalhe"):
            rename_map[col] = "Detalhe"
        elif "identificador" in c:
            rename_map[col] = "Identificador"
        elif c.startswith("valor"):
            rename_map[col] = "Valor"
        elif "observa" in c:
            rename_map[col] = "Observação"
    df = df.rename(columns=rename_map)
    for c in ["Data","Tipo","Detalhe","Identificador","Valor","Observação"]:
        if c not in df.columns:
            df[c] = ""
    df["Valor_raw"] = df["Valor"]
    df["Valor"] = clean_val(df["Valor"])
    return df[["Data","Tipo","Detalhe","Identificador","Valor","Observação","Valor_raw"]]

def consolidate(files: List[Tuple[str, bytes]]) -> pd.DataFrame:
    frames = []
    for name, content in files:
        df = read_bs2_csv(content)
        if not df.empty:
            df = df.copy()
            df["Arquivo"] = name
            frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["Arquivo","Data","Tipo","Detalhe","Identificador","Valor","Observação","Valor_raw"])
    return pd.concat(frames, ignore_index=True)

def filter_and_totals(df: pd.DataFrame):
    if df.empty:
        return df.copy(), df.copy(), 0.0, 0.0
    tipo_norm = df["Tipo"].map(normalize_text)
    tarifa_mask = tipo_norm.str.contains("tarifa operacoes pix", na=False)
    devol_mask = tipo_norm.str.contains("devolucao recebida pix", na=False)
    tarifa = df[tarifa_mask].copy()
    devol = df[devol_mask].copy()
    total_tarifa = float(tarifa["Valor"].sum()) if not tarifa.empty else 0.0
    total_devol = float(devol["Valor"].sum()) if not devol.empty else 0.0
    return tarifa, devol, total_tarifa, total_devol

def to_excel_bytes(total_tarifa: float, total_devol: float, devol: pd.DataFrame, tarifa: pd.DataFrame) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame({
            "Transação": ["Total Tarifa Operações Pix", "Total Devolução Recebida Pix"],
            "Valor Total (R$)": [round(total_tarifa, 2), round(total_devol, 2)]
        }).to_excel(writer, index=False, sheet_name="Resumo Tarifa Pix")

        if not devol.empty:
            cols = [c for c in ["Arquivo","Data","Tipo","Detalhe","Identificador","Valor","Observação"] if c in devol.columns]
            devol.sort_values(by="Data", inplace=True, na_position="last")
            devol[cols].to_excel(writer, index=False, sheet_name="Devolução Recebida Pix")
        else:
            pd.DataFrame(columns=["Arquivo","Data","Tipo","Detalhe","Identificador","Valor","Observação"]).to_excel(writer, index=False, sheet_name="Devolução Recebida Pix")

        if not tarifa.empty:
            cols_t = [c for c in ["Arquivo","Data","Tipo","Detalhe","Identificador","Valor","Observação"] if c in tarifa.columns]
            tarifa.sort_values(by="Data", inplace=True, na_position="last")
            tarifa[cols_t].to_excel(writer, index=False, sheet_name="Detalhe Tarifas Pix")
        else:
            pd.DataFrame(columns=["Arquivo","Data","Tipo","Detalhe","Identificador","Valor","Observação"]).to_excel(writer, index=False, sheet_name="Detalhe Tarifas Pix")
    return output.getvalue()

# ---------- UI ----------
st.title("💳 Pix CSV Uploader (BS2) – Consolidador")
st.caption("Envie múltiplos CSVs do extrato BS2; o app consolida automaticamente e gera um Excel com 3 abas.")

if "files" not in st.session_state:
    st.session_state["files"] = []  # list of (name, bytes)

with st.container(border=True):
    st.subheader("Upload de arquivos")
    files = st.file_uploader("Arraste ou selecione um ou mais CSVs", type=["csv"], accept_multiple_files=True, help="Formato: CSV delimitado por ponto-e-vírgula (;)")
    colA, colB = st.columns([1,1])
    with colA:
        if st.button("➕ Adicionar à consolidação", type="primary", disabled=not files):
            added = 0
            existing_keys = set((n, len(b)) for n,b in st.session_state["files"])
            for f in files or []:
                content = f.getvalue()
                key = (f.name, len(content))
                if key not in existing_keys:
                    st.session_state["files"].append((f.name, content))
                    added += 1
            st.success(f"{added} arquivo(s) adicionado(s).")
    with colB:
        if st.button("🗑️ Limpar consolidação", type="secondary", help="Remove todos os arquivos já adicionados"):
            st.session_state["files"] = []
            st.info("Consolidação limpa.")

    # Preview
    if st.session_state["files"]:
        st.write(f"**Arquivos na consolidação:** {len(st.session_state['files'])}")
        for i, (name, content) in enumerate(st.session_state["files"], start=1):
            st.write(f"{i}. {name} — {len(content)/1024:.1f} KB")

# Processamento
if st.session_state["files"]:
    df_all = consolidate(st.session_state["files"])
    tarifa, devol, total_tarifa, total_devol = filter_and_totals(df_all)

    st.divider()
    st.subheader("Indicadores consolidados")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Arquivos", len(st.session_state["files"]))
    with c2:
        st.metric("Linhas totais", int(df_all.shape[0]))
    with c3:
        st.metric("Total Tarifa Pix (R$)", f"{total_tarifa:,.2f}")
    with c4:
        st.metric("Total Devolução Pix (R$)", f"{total_devol:,.2f}")

    # Resumo por arquivo
    st.markdown("### Resumo por arquivo")
    if not df_all.empty:
        df_tmp = df_all.copy()
        df_tmp["Tipo_norm"] = df_tmp["Tipo"].map(normalize_text)
        resumo_arquivo = df_tmp.groupby("Arquivo").apply(
            lambda g: pd.Series({
                "linhas": int(g.shape[0]),
                "total_tarifa": float(g.loc[g["Tipo_norm"].eq("tarifa operacoes pix"), "Valor"].sum()),
                "total_devol": float(g.loc[g["Tipo_norm"].eq("devolucao recebida pix"), "Valor"].sum()),
            })
        ).reset_index()
        st.dataframe(resumo_arquivo, use_container_width=True)

    with st.expander("Ver prévia de Devoluções"):
        if devol.empty:
            st.info("Nenhuma Devolução Recebida Pix encontrada.")
        else:
            st.dataframe(devol.head(50), use_container_width=True)

    with st.expander("Ver prévia de Tarifas"):
        if tarifa.empty:
            st.info("Nenhuma Tarifa Operações Pix encontrada.")
        else:
            st.dataframe(tarifa.head(50), use_container_width=True)

    excel_bytes = to_excel_bytes(total_tarifa, total_devol, devol, tarifa)
    st.download_button(
        "⬇️ Baixar Excel consolidado",
        data=excel_bytes,
        file_name="resultado_pix_consolidado.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary"
    )
else:
    st.info("Adicione arquivos para ver os indicadores e habilitar o download do Excel.")
