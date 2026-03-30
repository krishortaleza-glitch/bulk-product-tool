import streamlit as st
import pandas as pd
from datetime import datetime
from io import BytesIO

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

def generate_keys(df, col, prefix):
    s = clean_upc_series(df[col])
    df[f"{prefix}_12"] = s.str.zfill(12)
    df[f"{prefix}_stripped"] = df[f"{prefix}_12"].str.lstrip("0")
    df[f"{prefix}_11"] = df[f"{prefix}_12"].str[-11:]

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
    # PROCESS BUTTON
    # ==============================
    if st.button("🚀 Process Files"):

        with st.spinner("Processing..."):

            # KEYS
            generate_keys(main_df, main_upc, "m")

            product_df["UPC_list"] = product_df[[product_upc1, product_upc2]].values.tolist()
            product_df = product_df.explode("UPC_list")

            generate_keys(product_df, "UPC_list", "p")

            # BUILD MAPS
            def build_map(df, key):
                return df.groupby(key).agg({
                    product_uid: lambda x: list(set(x)),
                    product_family: lambda x: list(set(x))
                }).rename(columns={
                    product_uid: "uids",
                    product_family: "families"
                })

            map_12 = build_map(product_df, "p_12")
            map_strip = build_map(product_df, "p_stripped")
            map_11 = build_map(product_df, "p_11")

            # MERGE
            merged = main_df.merge(map_12, how="left", left_on="m_12", right_index=True)
            merged = merged.merge(map_strip, how="left", left_on="m_stripped", right_index=True, suffixes=("", "_s"))
            merged = merged.merge(map_11, how="left", left_on="m_11", right_index=True, suffixes=("", "_11"))

            def combine_cols(col1, col2, col3):
                result = []
                for a, b, c in zip(col1, col2, col3):
                    vals = []
                    for v in (a, b, c):
                        if isinstance(v, list):
                            vals.extend(v)
                    vals = list(set(vals))
                    result.append(vals if vals else None)
                return result

            merged["All Retail UIDs"] = combine_cols(
                merged["uids"], merged["uids_s"], merged["uids_11"]
            )

            merged["All Families"] = combine_cols(
                merged["families"], merged["families_s"], merged["families_11"]
            )

            merged["Retail UID"] = merged["All Retail UIDs"].apply(
                lambda x: x[0] if isinstance(x, list) else None
            )

            merged["Family"] = merged["All Families"].apply(
                lambda x: x[0] if isinstance(x, list) else None
            )

            merged["Multiple Matches"] = merged["All Retail UIDs"].apply(
                lambda x: len(x) > 1 if isinstance(x, list) else False
            )

            merged["Match Type"] = merged["Retail UID"].apply(
                lambda x: "UPC Match" if pd.notna(x) else "No Match"
            )

            merged["All Retail UIDs"] = merged["All Retail UIDs"].apply(
                lambda x: ", ".join(map(str, x)) if isinstance(x, list) else None
            )

            merged["All Families"] = merged["All Families"].apply(
                lambda x: ", ".join(map(str, x)) if isinstance(x, list) else None
            )

            # STORE VALIDATION
            merged["store_family_key"] = (
                merged[main_store].astype(str) + "|" + merged["Family"].astype(str)
            )

            sf_df["store_family_key"] = (
                sf_df[sf_store].astype(str) + "|" + sf_df[sf_family].astype(str)
            )

            valid_keys = set(sf_df["store_family_key"])
            merged["Valid Store-Family"] = merged["store_family_key"].isin(valid_keys)

            # OUTPUTS
            good_df = merged[
                (merged["Retail UID"].notna()) &
                (merged["Valid Store-Family"])
            ][[main_store, "Retail UID"]].drop_duplicates()

            good_df.columns = ["Store", "Retail UID"]

            invalid_df = merged[
                (merged["Retail UID"].isna()) |
                (~merged["Valid Store-Family"])
            ].copy()

            invalid_df["Reason"] = invalid_df.apply(
                lambda r: "No Match" if pd.isna(r["Retail UID"])
                else "Invalid Store-Family" if not r["Valid Store-Family"]
                else "Multiple Matches" if r["Multiple Matches"]
                else "",
                axis=1
            )

            invalid_df = invalid_df[[main_store, main_upc, main_desc, "Reason"]]
            invalid_df.columns = ["Store", "UPC", "Description", "Reason"]

            unmatched_df = merged[merged["Match Type"] == "No Match"][
                [main_upc, main_desc]
            ]
            unmatched_df.columns = ["UPC", "Description"]

            invalid_sf_df = merged[~merged["Valid Store-Family"]][
                [main_store, "Family"]
            ].drop_duplicates()
            invalid_sf_df.columns = ["Store", "Family"]

            summary = merged["Match Type"].value_counts().reset_index()
            summary.columns = ["Match Type", "Count"]

            # EXPORT
            output = BytesIO()

            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                merged.to_excel(writer, sheet_name="Full Output", index=False)
                summary.to_excel(writer, sheet_name="Summary", index=False)
                good_df.to_excel(writer, sheet_name="Good To Go", index=False)
                invalid_df.to_excel(writer, sheet_name="Invalid For Portal", index=False)
                unmatched_df.to_excel(writer, sheet_name="Unmatched Products", index=False)
                invalid_sf_df.to_excel(writer, sheet_name="Invalid Store Family", index=False)

            output.seek(0)

        st.success("Done!")

        st.download_button(
            label="📥 Download Processed File",
            data=output,
            file_name=f"processed_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        )