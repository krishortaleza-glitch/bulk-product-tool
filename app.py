import streamlit as st
import pandas as pd
from datetime import datetime
from io import BytesIO
from rapidfuzz import fuzz

st.set_page_config(page_title="Bulk Product Request Tool", layout="wide")
st.title("📦 Bulk Product Request Tool")

# ==============================
# HELPERS
# ==============================
def clean_upc_series(series):
    series = series.astype(str)
    series = series.str.replace(r"\.0$", "", regex=True)
    series = series.str.replace(r"\D", "", regex=True)
    return series

def clean_desc(series):
    return series.astype(str).str.lower().str.strip()

def generate_keys(df, col, prefix):
    s = clean_upc_series(df[col])
    df[f"{prefix}_12"] = s.str.zfill(12)
    df[f"{prefix}_stripped"] = df[f"{prefix}_12"].str.lstrip("0")
    df[f"{prefix}_11"] = df[f"{prefix}_12"].str[-11:]
    df[f"{prefix}_10"] = df[f"{prefix}_12"].str[-10:]

def combine_cols(*cols):
    result = []
    for values in zip(*cols):
        vals = []
        for v in values:
            if isinstance(v, list):
                vals.extend(v)
        vals = list(set(vals))
        result.append(vals if vals else None)
    return result

# ==============================
# FILE UPLOAD
# ==============================
st.header("Upload Files")

adm_file = st.file_uploader("ADM File", type=["xlsx"])
product_file = st.file_uploader("Product File", type=["xlsx"])
store_file = st.file_uploader("Store Assignment File", type=["xlsx"])

if adm_file and product_file and store_file:

    main_df = pd.read_excel(adm_file)
    product_df = pd.read_excel(product_file)
    sf_df = pd.read_excel(store_file)

    st.success("Files loaded successfully")

    # ==============================
    # COLUMN SELECTORS
    # ==============================
    st.header("Select Columns")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.subheader("Main File")
        main_upc = st.selectbox("UPC Column", main_df.columns)
        main_desc = st.selectbox("Description Column", main_df.columns)
        main_store = st.selectbox("Store Column", main_df.columns)

    with col2:
        st.subheader("Product File")
        product_upc1 = st.selectbox("Product UPC 1", product_df.columns)
        product_upc2 = st.selectbox("Product UPC 2", product_df.columns)
        product_desc = st.selectbox("Product Description", product_df.columns)
        product_uid = st.selectbox("Product UID", product_df.columns)
        product_family = st.selectbox("Product Family", product_df.columns)

    with col3:
        st.subheader("Store File")
        sf_store = st.selectbox("Store Column (Store File)", sf_df.columns)
        sf_family = st.selectbox("Family Column (Store File)", sf_df.columns)

    # ==============================
    # PROCESS
    # ==============================
    if st.button("🚀 Process Files"):

        with st.spinner("Processing..."):

            main_df["desc_clean"] = clean_desc(main_df[main_desc])
            product_df["desc_clean"] = clean_desc(product_df[product_desc])

            generate_keys(main_df, main_upc, "m")

            product_df["UPC_list"] = product_df[[product_upc1, product_upc2]].values.tolist()
            product_df = product_df.explode("UPC_list")

            generate_keys(product_df, "UPC_list", "p")

            # ==============================
            # EXACT MATCH
            # ==============================
            def build_map(df, key):
                return df.groupby(key).agg({
                    product_uid: lambda x: list(set(x)),
                    product_family: lambda x: list(set(x))
                })

            map_12 = build_map(product_df, "p_12")

            merged = main_df.merge(map_12, how="left", left_on="m_12", right_index=True)

            merged["All Retail UIDs"] = merged[product_uid]
            merged["All Families"] = merged[product_family]

            # ==============================
            # 🔥 FUZZY 10-DIGIT MATCH
            # ==============================
            product_df["p_12_str"] = product_df["p_12"].astype(str)

            def fuzzy_match(row):
                if isinstance(row["All Retail UIDs"], list):
                    return row["All Retail UIDs"], row["All Families"], 100, "UPC Match"

                upc10 = row["m_10"]
                desc = row["desc_clean"]

                candidates = product_df[
                    product_df["p_12_str"].str.contains(upc10, na=False)
                ]

                best_score = 0
                best_uid = None
                best_family = None

                all_uids = []
                all_families = []

                for _, r in candidates.iterrows():
                    score = fuzz.partial_ratio(desc, r["desc_clean"])

                    if score >= 70:
                        all_uids.append(r[product_uid])
                        all_families.append(r[product_family])

                        if score > best_score:
                            best_score = score
                            best_uid = r[product_uid]
                            best_family = r[product_family]

                if not all_uids:
                    return None, None, 0, "No Match"

                return (
                    list(set(all_uids)),
                    list(set(all_families)),
                    best_score,
                    "10-digit Fuzzy Match"
                )

            results = merged.apply(fuzzy_match, axis=1)

            merged["All Retail UIDs"] = results.apply(lambda x: x[0])
            merged["All Families"] = results.apply(lambda x: x[1])
            merged["Match Score"] = results.apply(lambda x: x[2])
            merged["Match Type"] = results.apply(lambda x: x[3])

            # ==============================
            # FINALIZE
            # ==============================
            merged["Retail UID"] = merged["All Retail UIDs"].apply(
                lambda x: x[0] if isinstance(x, list) else None
            )

            merged["Family"] = merged["All Families"].apply(
                lambda x: x[0] if isinstance(x, list) else None
            )

            merged["Multiple Matches"] = merged["All Retail UIDs"].apply(
                lambda x: len(x) > 1 if isinstance(x, list) else False
            )

            merged["All Retail UIDs"] = merged["All Retail UIDs"].apply(
                lambda x: ", ".join(map(str, x)) if isinstance(x, list) else None
            )

            merged["All Families"] = merged["All Families"].apply(
                lambda x: ", ".join(map(str, x)) if isinstance(x, list) else None
            )

            # ==============================
            # STORE VALIDATION
            # ==============================
            merged["store_family_key"] = (
                merged[main_store].astype(str) + "|" + merged["Family"].astype(str)
            )

            sf_df["store_family_key"] = (
                sf_df[sf_store].astype(str) + "|" + sf_df[sf_family].astype(str)
            )

            valid_keys = set(sf_df["store_family_key"])
            merged["Valid Store-Family"] = merged["store_family_key"].isin(valid_keys)

            # ==============================
            # EXPORT
            # ==============================
            output = BytesIO()

            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                merged.to_excel(writer, sheet_name="Full Output", index=False)

            output.seek(0)

        st.success("Done!")

        st.download_button(
            label="📥 Download Processed File",
            data=output,
            file_name=f"processed_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        )
