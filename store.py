# -*- coding: utf-8 -*-
import pandas as pd
import requests
import pyodbc

DSN = (
    "DRIVER={HDBODBC};"
    "SERVERNODE=10.11.2.25:30241;"
    "UID=BAOJIANFENG;"
    "PWD=Xja@2025ABC;"
)

def chunk_list(lst, size=200):
    for i in range(0, len(lst), size):
        yield lst[i:i+size]

# =========================
# MES
# =========================
def get_chassis():
    url = "https://firebase-api-2mx9.onrender.com/api/mes-schedule"
    data = requests.get(url).json()["schedule"]

    df = pd.DataFrame(data)

    df["Chassis"] = df["Chassis"].astype(str).str.strip().str.upper()
    df["RegentProduction"] = df["RegentProduction"].fillna("").str.strip().str.lower()

    df["Is_Sea"] = df["RegentProduction"].apply(
        lambda x: "YES" if x == "van on the sea" else "NO"
    )

    df = df[
        ~df["RegentProduction"].isin([
            "",
            "none",
            "production commenced longtree"
        ])
    ]

    df = df[~df["Chassis"].str.startswith(("SRV", "SRM"))]

    return df[["Chassis", "Is_Sea"]].drop_duplicates()

# =========================
# Production Order
# =========================
def get_production_orders(chassis_list):
    all_df = []

    for chunk in chunk_list(chassis_list):
        chassis_sql = ",".join([f"'{c}'" for c in chunk])

        sql = f"""
        SELECT
            objk."SERNR" AS "Chassis",
            afko."AUFNR" AS "ProductionOrder"
        FROM SAPHANADB.SER02 s2
        JOIN SAPHANADB.OBJK objk
          ON objk."OBKNR" = s2."OBKNR"
         AND objk."MANDT" = '800'
        JOIN SAPHANADB.AFPO afpo
          ON afpo."KDAUF" = s2."SDAUFNR"
         AND afpo."MANDT" = '800'
        JOIN SAPHANADB.AFKO afko
          ON afko."AUFNR" = afpo."AUFNR"
         AND afko."MANDT" = '800'
        WHERE objk."SERNR" IN ({chassis_sql})
        """

        with pyodbc.connect(DSN) as conn:
            df = pd.read_sql(sql, conn)

        if not df.empty:
            all_df.append(df)

    return pd.concat(all_df, ignore_index=True) if all_df else pd.DataFrame()

# =========================
# RESB
# =========================
def get_resb_data(po_df):
    if po_df.empty:
        return pd.DataFrame()

    orders = po_df["ProductionOrder"].dropna().unique().tolist()
    all_df = []

    for chunk in chunk_list(orders):
        order_sql = ",".join([f"'{o}'" for o in chunk])

        sql = f"""
        SELECT
            r."AUFNR" AS "ProductionOrder",
            r."MATNR" AS "Part",
            makt."MAKTX" AS "Description",
            r."BDMNG" AS "RequiredQty",
            r."ENMNG" AS "IssuedQty",
            r."BDMNG" - r."ENMNG" AS "OpenQty"
        FROM SAPHANADB.RESB r
        LEFT JOIN SAPHANADB.MAKT makt
          ON makt."MATNR" = r."MATNR"
         AND makt."SPRAS" = 'E'
         AND makt."MANDT" = '800'
        WHERE r."WERKS" = '3111'
          AND r."MANDT" = '800'
          AND r."AUFNR" IN ({order_sql})
          AND COALESCE(r."XLOEK",'') <> 'X'
        """

        with pyodbc.connect(DSN) as conn:
            df = pd.read_sql(sql, conn)

        if not df.empty:
            all_df.append(df)

    return pd.concat(all_df, ignore_index=True) if all_df else pd.DataFrame()

# =========================
# Kanban
# =========================
def get_kanban_parts():
    sql = """
    SELECT DISTINCT MATNR
    FROM SAPHANADB.PKHD
    WHERE WERKS='3111' AND MANDT='800'
    """
    with pyodbc.connect(DSN) as conn:
        df = pd.read_sql(sql, conn)
    return set(df["MATNR"].astype(str).str.strip())

# =========================
# Kanban Extra（Lead Time / Safety Stock）
# =========================
# =========================
# Kanban Extra（Lead Time / Safety Stock）
# =========================
def get_kanban_extra():
    sql = """
    SELECT
        marc."MATNR" AS "Part",
        marc."PLIFZ"  AS "LeadTime",
        marc."EISBE"  AS "SafetyStock"
    FROM SAPHANADB.MARC marc
    WHERE marc."WERKS" = '3111'
      AND marc."MANDT" = '800'
    """
    with pyodbc.connect(DSN) as conn:
        df = pd.read_sql(sql, conn)
    df["Part"] = df["Part"].astype(str).str.strip()
    return df

# =========================
# Inventory
# =========================
def get_inventory():
    sql = """
    SELECT MATNR AS "Part", SUM(LABST) AS "StockQty"
    FROM SAPHANADB.NSDM_V_MARD
    WHERE WERKS='3111'
      AND LGORT='0001'
      AND MANDT='800'
    GROUP BY MATNR
    """
    with pyodbc.connect(DSN) as conn:
        return pd.read_sql(sql, conn)

# =========================
# Open PO（含下单时间）
# =========================
def get_open_po_details():
    sql = """
    SELECT
        ekpo."EBELN",
        ekpo."EBELP",
        ekpo."MATNR"               AS "Part",
        ekpo."TXZ01"               AS "Description",
        ekko."LIFNR"               AS "Vendor",
        ekko."EKGRP"               AS "PurchasingGroup",
        ekko."BEDAT"               AS "OrderDate",
        eket."EINDT"               AS "DeliveryDate",
        eket."MENGE"               AS "OrderQty",
        COALESCE(eket."WEMNG",0)   AS "ReceivedQty",
        eket."MENGE" - COALESCE(eket."WEMNG",0) AS "OpenQty"
    FROM SAPHANADB.EKPO ekpo
    JOIN SAPHANADB.EKET eket
      ON ekpo."EBELN" = eket."EBELN"
     AND ekpo."EBELP" = eket."EBELP"
    JOIN SAPHANADB.EKKO ekko
      ON ekpo."EBELN" = ekko."EBELN"
    WHERE ekpo."WERKS"='3111'
      AND ekpo."MANDT"='800'
      AND eket."MANDT"='800'
      AND ekko."MANDT"='800'
      AND ekpo."EBELN" NOT LIKE '70000%'
      AND COALESCE(ekko."LOEKZ",'') <> 'L'
      AND COALESCE(ekpo."LOEKZ",'') <> 'L'
      AND COALESCE(ekpo."ELIKZ",'') <> 'X'
      AND COALESCE(eket."MENGE",0) > 0
      AND (eket."MENGE" - COALESCE(eket."WEMNG",0)) > 0
    """
    with pyodbc.connect(DSN) as conn:
        return pd.read_sql(sql, conn)

# =========================
# Build dataset
# =========================
def build_dataset(chassis_df):
    po_df = get_production_orders(chassis_df["Chassis"].tolist())
    resb_df = get_resb_data(po_df)

    df = po_df.merge(resb_df, on="ProductionOrder", how="left")
    df = df.merge(chassis_df, on="Chassis", how="left")

    kanban = get_kanban_parts()
    df["Part"] = df["Part"].astype(str).str.strip()
    df["Is_Kanban"] = df["Part"].apply(lambda x: "YES" if x in kanban else "NO")

    return df

# =========================
# Summary
# =========================
def build_summary(data, inventory_df):

    summary = data.groupby(
        ["Part", "Description"],
        as_index=False
    ).agg({
        "RequiredQty": "sum",
        "IssuedQty": "sum",
        "OpenQty": "sum"
    })

    kanban_flag = data.groupby("Part")["Is_Kanban"].apply(
        lambda x: "YES" if "YES" in x.values else "NO"
    ).reset_index()

    summary = summary.merge(kanban_flag, on="Part", how="left")
    summary = summary.merge(inventory_df, on="Part", how="left")

    summary["StockQty"] = summary["StockQty"].fillna(0)
    summary["NetShortage"] = summary["OpenQty"] - summary["StockQty"]
    summary["Risk"] = summary["NetShortage"].apply(
        lambda x: "⚠️ HIGH" if x > 0 else "OK"
    )

    return summary

# =========================
# Excel
# =========================
def export_excel(df):

    inventory_df = get_inventory()
    open_po_df   = get_open_po_details()

    writer = pd.ExcelWriter("production_report.xlsx", engine="xlsxwriter")

    # -------------------------------------------------------
    # 📋 Details（去掉 Part 是 NA 的行）
    # -------------------------------------------------------
    detail_df = df[
        df["Part"].notna() &
        (df["Part"].astype(str).str.strip() != "") &
        (df["Part"].astype(str).str.lower() != "nan")
    ].copy()
    detail_df.to_excel(writer, "Details", index=False)

    # -------------------------------------------------------
    # 📊 Summary_No_Sea / Summary_With_Sea（去掉 Part 以 D14 开头）
    # -------------------------------------------------------
    summary_df = df[~df["Part"].astype(str).str.strip().str.upper().str.startswith("D14")].copy()

    build_summary(
        summary_df[summary_df["Is_Sea"] == "NO"],
        inventory_df
    ).to_excel(writer, "Summary_No_Sea", index=False)

    build_summary(
        summary_df,
        inventory_df
    ).to_excel(writer, "Summary_With_Sea", index=False)

    # -------------------------------------------------------
    # 🔥 Kanban_Analysis
    # -------------------------------------------------------
    kanban_df = df[df["Is_Kanban"] == "YES"].copy()

    kanban_summary = kanban_df.groupby(
        ["Part", "Description"],
        as_index=False
    ).agg({
        "RequiredQty": "sum",
        "IssuedQty":   "sum",
        "OpenQty":     "sum"
    })

    # 👉 加库存
    kanban_summary = kanban_summary.merge(
        inventory_df,
        on="Part",
        how="left"
    )

    # 👉 加 Open PO（含下单时间）
    # open_po_df 已在上面 fetch，直接复用，不重复查 SAP
    po_summary = open_po_df.groupby("Part", as_index=False).agg(
        OpenPOQty=("OpenQty",      "sum"),
        OrderDate=("OrderDate",    "min"),   # 下单时间：最早的 PO 创建日期
    )

    kanban_summary = kanban_summary.merge(
        po_summary,
        on="Part",
        how="left"
    )

    # 👉 加 Lead Time / Safety Stock
    extra_df = get_kanban_extra()
    kanban_summary = kanban_summary.merge(
        extra_df,
        on="Part",
        how="left"
    )

    # 👉 清理空值（数值列填0，日期列保持空白）
    for col in kanban_summary.columns:
        if col == "OrderDate":
            continue
        if kanban_summary[col].dtype in ["float64", "int64", "Int64"]:
            kanban_summary[col] = kanban_summary[col].fillna(0)

    # 👉 排序
    kanban_summary = kanban_summary.sort_values(
        by=["OpenQty"],
        ascending=False
    )

    kanban_summary.to_excel(writer, "Kanban_Analysis", index=False)

    # -------------------------------------------------------
    # 📦 Open_PO_Details（去掉 Part 以 D14 开头）
    # -------------------------------------------------------
    open_po_filtered_df = open_po_df[
        ~open_po_df["Part"].astype(str).str.strip().str.upper().str.startswith("D14")
    ].copy()
    open_po_filtered_df.to_excel(writer, "Open_PO_Details", index=False)

    writer.close()

# =========================
# Main
# =========================
def main():

    chassis_df = get_chassis()

    if chassis_df.empty:
        print("没有数据")
        return

    df = build_dataset(chassis_df)

    if df.empty:
        print("没有结果")
        return

    export_excel(df)

    print("✅ 完成 production_report.xlsx")

if __name__ == "__main__":
    main()
