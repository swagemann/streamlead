# app.py
import streamlit as st
import plotly.express as px
import pandas as pd
from ado_client import get_credential, get_ado_connection, fetch_work_items, ADO_SCOPE
from teams import load_teams, save_teams, add_member, remove_member

st.set_page_config(page_title="ADO Dashboard", layout="wide")
st.title("Data Modeling Management Dashboard")

# --- Hardcoded Area Paths ---
AREA_PATHS = [
    "Information Management\\BI Dev",
    "Information Management\\dbt",
    # "Information Management\\BI Outbound",
    # "Information Management\\Business Intelligence",
]

# --- Session state for auth ---
if "credential" not in st.session_state:
    st.session_state.credential = None

# --- Sidebar: Config & Filters ---
with st.sidebar:
    st.header("Configuration")
    org_url = st.text_input("Org URL", value="https://dev.azure.com/HOLMAN")
    project = st.text_input("Project", value="IT")

    if st.session_state.credential is None:
        if st.button("Sign In with Microsoft"):
            try:
                cred = get_credential()
                cred.get_token(ADO_SCOPE)
                st.session_state.credential = cred
                st.rerun()
            except Exception as e:
                st.error(f"Authentication failed: {e}")
    else:
        st.success("Signed in")
        if st.button("Sign Out"):
            st.session_state.credential = None
            st.rerun()

    st.divider()
    st.header("Timeframe")
    date_range = st.date_input("Date Range", value=[
        pd.Timestamp.now() - pd.Timedelta(days=180),
        pd.Timestamp.now()
    ])

    st.divider()
    st.header("Filters")

# --- Data Fetch (cached) ---
@st.cache_data(ttl=300)
def load_data(org_url, token, project, start_date, end_date, area_paths):
    conn = get_ado_connection(org_url, token)
    area_clause = " OR ".join(
        f"[System.AreaPath] UNDER '{project}\\{ap}'" for ap in area_paths
    )
    wiql = f"""
        SELECT [System.Id] FROM WorkItems
        WHERE [System.TeamProject] = '{project}'
        AND ({area_clause})
        AND [System.CreatedDate] >= '{start_date}'
        AND [System.CreatedDate] <= '{end_date}'
        ORDER BY [System.CreatedDate] DESC
    """
    return fetch_work_items(conn, project, wiql)


if st.session_state.credential and project:
    token = st.session_state.credential.get_token(ADO_SCOPE).token
    df = load_data(org_url, token, project, str(date_range[0]), str(date_range[1]), tuple(AREA_PATHS))
    teams = load_teams()

    # Derive tags_list
    df["tags_list"] = df["tags"].apply(
        lambda t: [x.strip() for x in t.split(";") if x.strip()] if t else []
    )

    # Build team lookup
    member_to_team = {}
    for team, members in teams.items():
        for m in members:
            member_to_team[m] = team
    df["team"] = df["assigned_to"].map(member_to_team).fillna("Unassigned")

    # Compute ticket age
    now = pd.Timestamp.now(tz="UTC")
    df["age_days"] = (now - df["created_date"]).dt.days

    # Sidebar filters (dynamic)
    with st.sidebar:
        status_filter = st.multiselect("Status", df["state"].unique(), default=list(df["state"].unique()))
        team_filter = st.multiselect("Team", df["team"].unique(), default=list(df["team"].unique()))
        type_filter = st.multiselect("Work Item Type", df["type"].unique(), default=list(df["type"].unique()))

        all_tags = sorted(set(tag for tags in df["tags_list"] for tag in tags))
        tag_filter = st.multiselect("Tags", all_tags, default=[])

    # Apply filters
    filtered = df[
        (df["state"].isin(status_filter)) &
        (df["team"].isin(team_filter)) &
        (df["type"].isin(type_filter))
    ]
    if tag_filter:
        filtered = filtered[
            filtered["tags_list"].apply(lambda t: bool(set(t) & set(tag_filter)))
        ]

    # --- KPI Row ---
    closed_items = filtered.dropna(subset=["closed_date"])
    days_to_close = closed_items.apply(
        lambda r: (r["closed_date"] - r["created_date"]).days, axis=1
    )
    median_days = days_to_close.median() if len(days_to_close) > 0 else None

    def count_area(keyword):
        return len(filtered[filtered["area_path"].str.contains(keyword, case=False, na=False)])

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Median Days to Close", round(median_days, 1) if pd.notna(median_days) else "N/A")
    c2.metric("dbt", count_area("dbt"))
    c3.metric("BI Dev", count_area("BI Dev"))
    c4.metric("IM-BI Outbound", count_area("BI Outbound"))
    c5.metric("IM Business Intelligence", count_area("Business Intelligence"))

    # --- Tickets Over Time (Bar Chart, full width) ---
    st.subheader("Tickets Over Time (Created vs Closed)")
    created_ts = (filtered.set_index("created_date")
                  .resample("W").size().reset_index(name="created"))
    closed_ts = (filtered.dropna(subset=["closed_date"])
                 .set_index("closed_date")
                 .resample("W").size().reset_index(name="closed"))
    ts = created_ts.merge(closed_ts, left_on="created_date",
                          right_on="closed_date", how="outer").fillna(0)
    ts["date"] = ts["created_date"].combine_first(ts["closed_date"])
    ts_melted = ts.melt(id_vars=["date"], value_vars=["created", "closed"],
                        var_name="series", value_name="count")
    fig = px.bar(ts_melted, x="date", y="count", color="series", barmode="group",
                 labels={"count": "Count", "date": "Week", "series": ""})
    fig.update_layout(xaxis_title="Week", yaxis_title="Count")
    st.plotly_chart(fig, use_container_width=True)

    # --- Tickets by Tag ---
    st.subheader("Tickets by Tag")
    tag_df = filtered.explode("tags_list")
    tag_df = tag_df[tag_df["tags_list"].astype(bool)]
    if not tag_df.empty:
        tag_counts = tag_df.groupby("tags_list").size().reset_index(name="count")
        tag_counts = tag_counts.sort_values("count", ascending=False).head(15)
        fig_tags = px.bar(tag_counts, x="tags_list", y="count", text_auto=True,
                          labels={"tags_list": "Tag", "count": "Count"})
        st.plotly_chart(fig_tags, use_container_width=True)
    else:
        st.info("No tagged items in the current filter.")

    # --- Team Summary Table ---
    st.divider()
    st.subheader("Team Summary")

    team_names = sorted(filtered["team"].unique())
    team_rows = []

    # States mapping
    new_states = ["New", "Created"]
    evaluate_states = ["Evaluate"]
    active_group_states = ["Approved", "Active", "In Progress"]
    complete_states = ["Closed", "Resolved", "Done", "Complete"]
    blocked_states = ["Blocked"]

    for t in team_names:
        t_df = filtered[filtered["team"] == t]

        t_closed = t_df[t_df["state"].isin(complete_states)]
        t_days = t_closed.apply(
            lambda r: (r["closed_date"] - r["created_date"]).days if pd.notna(r["closed_date"]) else None, axis=1
        ).dropna()

        # Open tickets age
        t_open = t_df[~t_df["state"].isin(complete_states)]
        older_week = len(t_open[t_open["age_days"] > 7])
        older_month = len(t_open[t_open["age_days"] > 30])

        avg_comments = t_df["comment_count"].mean() if "comment_count" in t_df.columns and t_df["comment_count"].notna().any() else None

        team_rows.append({
            "Team": t,
            "Created": int(len(t_df[t_df["state"].isin(new_states)])),
            "Evaluate": int(len(t_df[t_df["state"].isin(evaluate_states)])),
            "Approved/Active/In Progress": int(len(t_df[t_df["state"].isin(active_group_states)])),
            "Complete": int(len(t_closed)),
            "Blocked": int(len(t_df[t_df["state"].isin(blocked_states)])),
            "Avg Days to Complete": round(t_days.mean(), 1) if len(t_days) > 0 else "N/A",
            "Avg Comments/Ticket": round(avg_comments, 1) if pd.notna(avg_comments) else "N/A",
            "Older Than Week": int(older_week),
            "Older Than Month": int(older_month),
        })

    team_summary_df = pd.DataFrame(team_rows)

    def highlight_old_columns(row):
        styles = [""] * len(row)
        cols = list(row.index)
        week_idx = cols.index("Older Than Week") if "Older Than Week" in cols else -1
        month_idx = cols.index("Older Than Month") if "Older Than Month" in cols else -1
        if month_idx >= 0 and row["Older Than Month"] > 0:
            styles[month_idx] = "background-color: #ffcccc; color: #8b0000"
        if week_idx >= 0 and row["Older Than Week"] > 0:
            styles[week_idx] = "background-color: #ffffcc; color: #8b8000"
        return styles

    styled_summary = team_summary_df.style.apply(highlight_old_columns, axis=1)
    st.dataframe(styled_summary, use_container_width=True, hide_index=True)

    # --- Team Ticket Details (replaces Kanban) ---
    st.divider()
    st.subheader("Team Ticket Details")

    selected_team = st.selectbox("Select Team", ["All"] + team_names)

    if selected_team == "All":
        detail_df = filtered
    else:
        detail_df = filtered[filtered["team"] == selected_team]

    # Status ordering
    state_order = ["New", "Created", "Evaluate", "Approved", "Active", "In Progress", "Blocked", "Resolved", "Closed", "Done", "Complete"]
    active_states_in_data = [s for s in state_order if s in detail_df["state"].unique()]
    for s in detail_df["state"].unique():
        if s not in active_states_in_data:
            active_states_in_data.append(s)

    def highlight_old_tickets(row):
        age = row.get("Age (Days)", 0)
        if pd.notna(age) and age > 30:
            return ["background-color: #ffcccc"] * len(row)
        elif pd.notna(age) and age > 7:
            return ["background-color: #ffffcc"] * len(row)
        return [""] * len(row)

    for state_name in active_states_in_data:
        state_items = detail_df[detail_df["state"] == state_name]
        if state_items.empty:
            continue

        st.markdown(f"### {state_name} ({len(state_items)})")

        display_df = state_items[["id", "title", "assigned_to", "type", "area_path", "created_date", "age_days", "comment_count", "tags"]].copy()
        display_df.columns = ["ID", "Title", "Assigned To", "Type", "Area Path", "Created", "Age (Days)", "Comments", "Tags"]
        display_df["Created"] = display_df["Created"].dt.strftime("%Y-%m-%d")

        styled = display_df.style.apply(highlight_old_tickets, axis=1)
        st.dataframe(styled, use_container_width=True, hide_index=True)

        st.divider()

    # --- Team Management ---
    st.divider()
    st.subheader("Team Management")

    tab1, tab2 = st.tabs(["View Teams", "Edit Teams"])

    with tab1:
        for team, members in teams.items():
            with st.expander(f"{team} ({len(members)} members)"):
                for m in members:
                    st.write(f"- {m}")

    with tab2:
        col_a, col_b = st.columns(2)
        with col_a:
            st.write("**Add Member**")
            team_name = st.text_input("Team Name")
            member = st.selectbox("Member", df["assigned_to"].dropna().unique())
            if st.button("Add"):
                add_member(team_name, member, teams)
                st.rerun()
        with col_b:
            st.write("**Remove Member**")
            del_team = st.selectbox("From Team", list(teams.keys()) or [""])
            if del_team and teams.get(del_team):
                del_member = st.selectbox("Member to Remove", teams[del_team])
                if st.button("Remove"):
                    remove_member(del_team, del_member, teams)
                    st.rerun()

else:
    st.info("Sign in with your Microsoft account and enter your project name in the sidebar to get started.")
