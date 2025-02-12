import os
import re
import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

# -----------------------------------------
# Dark Theme CSS (no forced black label for selectbox)
# -----------------------------------------
DARK_CSS = """
<style>
/* Overall dark background */
body, .stApp {
    background-color: #121212 !important;
    color: #e0e0e0 !important;
}
/* Dark sidebar */
[data-testid="stSidebar"] {
    background-color: #1f1f1f !important;
}
/* General text in the app */
h1, h2, h3, h4, h5, label, span, div,
.css-10trblm, .css-1vbd788 {
    color: #e0e0e0 !important;
}
/* Make the SELECTBOX (if still used) background white with black text */
.css-1wa3eu0, .css-1r5dzl0, .css-1inwz65, .css-1hb9hxy {
    background-color: #ffffff !important;
    color: #000000 !important;
}
.css-1uccc91-singleValue, .css-qrbaxs-DropdownOption {
    color: #000000 !important;
}
/* For radio button labels, force dark text */
div[data-testid="stRadio"] > label {
    color: #000000 !important;
}
</style>
"""
st.set_page_config(page_title="Engineering Project Dashboard", layout="wide")
st.markdown(DARK_CSS, unsafe_allow_html=True)

# -----------------------------------------
# File Uploader for Excel Data
# -----------------------------------------
st.sidebar.header("Upload Data")
uploaded_file = st.sidebar.file_uploader("Upload Production.xlsx", type=["xlsx", "xls"])

def parse_duration(val):
    """Attempts to convert a duration value to float by extracting digits."""
    try:
        return float(val)
    except Exception:
        m = re.search(r"(\d+(\.\d+)?)", str(val))
        if m:
            return float(m.group(1))
        return None

@st.cache_data
def load_data(file) -> pd.DataFrame:
    """
    1) Reads Excel from the uploaded file.
    2) Normalizes 'Name' for 'FAT'/'Shipping'.
    3) Extends zero-day FAT/Shipping tasks to 3 days.
    4) Tags each row with 'Project' (each Manually Scheduled row starts a new project).
    5) Adds 'Label' = 'Name (Project)' for Gantt chart text.
    6) Parses the 'Duration' column to numeric.
    """
    df = pd.read_excel(file)

    # 1) Normalize 'Name'
    if "Name" in df.columns:
        df["Name"] = df["Name"].astype(str).str.strip().fillna("")
        df["Name"] = df["Name"].replace({
            r"(?i)^\s*fat\s*$": "FAT",
            r"(?i)^\s*shipping\s*$": "Shipping"
        }, regex=True)

    # 2) Convert Start/Finish to datetime
    if "Start" in df.columns:
        df["Start"] = pd.to_datetime(df["Start"], errors="coerce")
    if "Finish" in df.columns:
        df["Finish"] = pd.to_datetime(df["Finish"], errors="coerce")

    df = df.reset_index(drop=True)

    # 3) Extend zero-day FAT/Shipping to 3 days
    if {"Name", "Start", "Finish"}.issubset(df.columns):
        mask_fs = df["Name"].isin(["FAT", "Shipping"]) & df["Start"].notna() & df["Finish"].notna()
        durations = (df["Finish"] - df["Start"]).dt.days
        zero_mask = mask_fs & (durations == 0)
        df.loc[zero_mask, "Finish"] = df.loc[zero_mask, "Finish"] + pd.Timedelta(days=3)

    # 4) Tag each row with the current "Project"
    df["Project"] = None
    current_project = None
    if "Task Mode" in df.columns and "Name" in df.columns:
        for i in df.index:
            if df.loc[i, "Task Mode"] == "Manually Scheduled":
                current_project = df.loc[i, "Name"]
            df.loc[i, "Project"] = current_project

    # 5) Label = "Name (Project)" -- ensure non-empty label
    def make_label(row):
        name_val = row["Name"] if row["Name"] != "" else "Unnamed"
        if pd.notnull(row["Project"]) and row["Project"] != "":
            return f"{name_val} ({row['Project']})"
        return name_val
    df["Label"] = df.apply(make_label, axis=1)

    # 6) Parse "Duration" column to numeric
    if "Duration" in df.columns:
        df["Duration"] = df["Duration"].apply(parse_duration)

    return df

if uploaded_file is None:
    st.info("Please upload the Production.xlsx file using the sidebar uploader.")
    st.stop()
else:
    df = load_data(uploaded_file)

# -----------------------------------------
# Sidebar Navigation
# -----------------------------------------
page = st.sidebar.radio("Navigation", ["Overall Dashboard", "Project Details"])

# -----------------------------------------
# Task -> Color Functions
# -----------------------------------------
def task_color(name: str) -> str:
    nm = name.strip().lower()
    if nm == "fat":
        return "purple"
    elif nm == "shipping":
        return "pink"
    elif nm in ["engineering", "drawing approval"]:
        return "darkblue"
    elif nm == "production preparation":
        return "green"
    elif nm == "production":
        return "yellow"
    elif nm == "assembly":
        return "orange"
    elif nm == "electrical wiring/testing":
        return "red"
    else:
        return "lightgray"

def build_color_map(data: pd.DataFrame) -> dict:
    cmap = {}
    for nm in data["Name"].unique():
        cmap[nm] = task_color(nm)
    return cmap

# -----------------------------------------
# Compute Completion Percentage for a Project
# -----------------------------------------
def compute_completion(start, finish):
    """Compute completion % based on current date relative to start and finish."""
    today = pd.Timestamp.now().normalize()
    if pd.isna(start) or pd.isna(finish) or start >= finish:
        return None
    total = (finish - start).days
    elapsed = (today - start).days
    pct = (elapsed / total) * 100
    return max(0, min(100, pct))

# -----------------------------------------
# Overlapping Auto-Scheduled Sub-Tasks (Different Projects)
# -----------------------------------------
def find_overlaps_subtasks(gantt_df: pd.DataFrame) -> pd.DataFrame:
    """
    Overlap only if same sub-task name in different projects (auto-scheduled).
    """
    data = gantt_df[
        (gantt_df["Task Mode"] == "Auto Scheduled") &
        (gantt_df["Start"].notna()) & (gantt_df["Finish"].notna())
    ].copy()
    data = data[["Name", "Project", "Start", "Finish"]].reset_index(drop=True)
    overlaps = []
    for i in range(len(data)):
        for j in range(i+1, len(data)):
            if data.loc[i, "Name"] != data.loc[j, "Name"]:
                continue
            if data.loc[i, "Project"] == data.loc[j, "Project"]:
                continue
            s_i, f_i = data.loc[i, "Start"], data.loc[i, "Finish"]
            s_j, f_j = data.loc[j, "Start"], data.loc[j, "Finish"]
            if s_i < f_j and s_j < f_i:
                overlap_start = max(s_i, s_j)
                overlap_end = min(f_i, f_j)
                overlaps.append({
                    "SubTask": data.loc[i, "Name"],
                    "ProjectA": data.loc[i, "Project"],
                    "ProjectB": data.loc[j, "Project"],
                    "OverlapStart": overlap_start,
                    "OverlapEnd": overlap_end
                })
    return pd.DataFrame(overlaps)

# -----------------------------------------
# PAGE 1: OVERALL DASHBOARD
# -----------------------------------------
if page == "Overall Dashboard":

    st.title("Engineering Project Dashboard")

    st.subheader("Summary Metrics")
    c1, c2, c3, c4, c5 = st.columns(5)
    # 1) Total Projects (manually scheduled)
    total_projects = 0
    if "Task Mode" in df.columns:
        total_projects = (df["Task Mode"] == "Manually Scheduled").sum()
    with c1:
        st.metric("Total Projects", total_projects)
    # 2) Total Project Duration (from manually scheduled rows)
    if "Duration" in df.columns:
        ms_df = df[df["Task Mode"] == "Manually Scheduled"].copy()
        ms_df["Duration"] = pd.to_numeric(ms_df["Duration"], errors="coerce")
        ms_df = ms_df.dropna(subset=["Duration"])
        total_proj_duration = ms_df["Duration"].sum()
        with c2:
            st.metric("Total Project Duration (days)", f"{total_proj_duration:.0f}")
    # 3) Average Duration (from manually scheduled rows)
    if "Duration" in df.columns:
        ms_df = df[df["Task Mode"] == "Manually Scheduled"].copy()
        ms_df["Duration"] = pd.to_numeric(ms_df["Duration"], errors="coerce")
        ms_df = ms_df.dropna(subset=["Duration"])
        if not ms_df.empty:
            avg_duration = ms_df["Duration"].mean()
            with c3:
                st.metric("Avg. Duration (days)", f"{avg_duration:.2f}")
        else:
            with c3:
                st.metric("Avg. Duration (days)", "N/A")
    # 4) Earliest Start
    if "Start" in df.columns and df["Start"].notna().any():
        earliest_start = df["Start"].min()
        with c4:
            st.metric("Earliest Start", earliest_start.strftime("%Y-%m-%d"))
    # 5) Latest Finish
    if "Finish" in df.columns and df["Finish"].notna().any():
        latest_finish = df["Finish"].max()
        with c5:
            st.metric("Latest Finish", latest_finish.strftime("%Y-%m-%d"))
    st.markdown("---")
    # Overall Project Duration Chart (Manually Entered)
    st.subheader("Overall Project Duration")
    if {"Task Mode", "Duration", "Project"}.issubset(df.columns):
        ms_df = df[df["Task Mode"] == "Manually Scheduled"].copy()
        ms_df["Duration"] = pd.to_numeric(ms_df["Duration"], errors="coerce")
        ms_df = ms_df.dropna(subset=["Duration"])
        if ms_df.empty:
            st.info("No manually scheduled projects with numeric Duration.")
        else:
            ms_df = ms_df.sort_values(by="Duration", ascending=True)
            dur_fig = px.bar(
                ms_df,
                x="Project",
                y="Duration",
                color="Project",
                title="Overall Project Duration",
                template="plotly_dark"
            )
            dur_fig.update_traces(
                marker_line_color="white",
                marker_line_width=1,
                textposition="auto",
                textfont_color="white"
            )
            dur_fig.update_layout(
                font_color="white",
                paper_bgcolor="#121212",
                plot_bgcolor="#121212",
                legend_font_color="white"
            )
            dur_fig.update_xaxes(color="white")
            dur_fig.update_yaxes(color="white")
            st.plotly_chart(dur_fig, use_container_width=True)
    else:
        st.info("Need 'Task Mode', 'Duration', 'Project' columns for Overall Project Duration chart.")
    st.markdown("---")
    # Overall Project Completion Chart as Donut Charts (live update)
    st.subheader("Project Completion (%)")
    st_autorefresh(interval=1000, key="completion_refresh")
    if {"Task Mode", "Start", "Finish", "Project"}.issubset(df.columns):
        ms_df = df[df["Task Mode"] == "Manually Scheduled"].copy()
        ms_df = ms_df.dropna(subset=["Start", "Finish"])
        if not ms_df.empty:
            today = pd.Timestamp.now().normalize()
            ms_df["Completion"] = ((today - ms_df["Start"]).dt.days / (ms_df["Finish"] - ms_df["Start"]).dt.days) * 100
            ms_df["Completion"] = ms_df["Completion"].clip(lower=0, upper=100)
            num_projects = len(ms_df)
            cols = st.columns(min(num_projects, 4))
            for idx, row in ms_df.iterrows():
                comp = row["Completion"]
                remaining = 100 - comp
                comp_df = pd.DataFrame({
                    "Status": ["Completed", "Remaining"],
                    "Value": [comp, remaining]
                })
                fig = px.pie(comp_df, names="Status", values="Value",
                             title=f"{row['Project']}",
                             hole=0.5, template="plotly_dark")
                fig.update_traces(textinfo='none', marker=dict(line=dict(color='#121212', width=1)))
                fig.update_layout(
                    showlegend=False,
                    margin=dict(l=10, r=10, t=30, b=10),
                    font_color="white",
                    paper_bgcolor="#121212",
                    plot_bgcolor="#121212"
                )
                cols[idx % 4].plotly_chart(fig, use_container_width=True)
        else:
            st.info("No scheduled projects with valid dates for Completion.")
    else:
        st.info("Need 'Task Mode', 'Start', 'Finish', 'Project' columns for Completion chart.")
    st.markdown("---")
    # Overall Gantt Chart
    st.subheader("Overall Gantt Chart")
    if {"Name", "Start", "Finish", "Label"}.issubset(df.columns):
        gantt_df = df.dropna(subset=["Start", "Finish"]).copy()
        gantt_df["sort_order"] = 1
        if "Task Mode" in gantt_df.columns:
            gantt_df.loc[gantt_df["Task Mode"] == "Manually Scheduled", "sort_order"] = 0
        gantt_df = gantt_df.sort_values(by=["sort_order", "Name"], ascending=[True, True])
        color_map = {nm: task_color(nm) for nm in gantt_df["Name"].unique()}
        gantt_fig = px.timeline(
            gantt_df,
            x_start="Start",
            x_end="Finish",
            y="Name",
            color="Name",
            color_discrete_map=color_map,
            title="Overall Gantt Chart (Colored by Name)",
            template="plotly_dark",
            text="Label"
        )
        gantt_fig.update_traces(
            marker_line_color="white",
            marker_line_width=1,
            textposition="inside",
            textfont_color="white"
        )
        gantt_fig.update_yaxes(autorange="reversed")
        gantt_fig.update_layout(
            font_color="white",
            paper_bgcolor="#121212",
            plot_bgcolor="#121212",
            legend_font_color="white"
        )
        gantt_fig.update_xaxes(color="white")
        gantt_fig.update_yaxes(color="white")
        st.plotly_chart(gantt_fig, use_container_width=True)
        st.subheader("Overlapping Sub-Tasks (Across Different Projects)")
        overlap_df = find_overlaps_subtasks(gantt_df)
        if overlap_df.empty:
            st.info("No overlapping sub-tasks with the same name across different projects.")
        else:
            st.dataframe(overlap_df)
    else:
        st.info("Need 'Name', 'Start', 'Finish', 'Label' columns for Gantt chart.")
# -----------------------------------------
# PAGE 2: PROJECT DETAILS
# -----------------------------------------
else:
    st.title("Individual Project Details")
    if "Task Mode" not in df.columns:
        st.warning("No 'Task Mode' column found.")
    else:
        manual_df = df[df["Task Mode"] == "Manually Scheduled"].copy()
        if manual_df.empty:
            st.info("No scheduled projects found.")
        else:
            st.markdown("**Pick a Project:**")
            # Using radio buttons for project selection for better visibility
            project_choice = st.radio("", manual_df["Name"].unique())
            proj_indices = df.index[(df["Name"] == project_choice) & (df["Task Mode"] == "Manually Scheduled")].tolist()
            if not proj_indices:
                st.warning("Project not found.")
            else:
                sel_idx = proj_indices[0]
                # Live countdown for current sub-task
                now = pd.Timestamp.now()
                next_idx_list = df.index[(df["Task Mode"] == "Manually Scheduled") & (df.index > sel_idx)].tolist()
                next_idx = next_idx_list[0] if next_idx_list else len(df)
                sub_tasks_all = df.iloc[sel_idx+1 : next_idx]
                sub_tasks_current = sub_tasks_all[
                    (sub_tasks_all["Task Mode"] == "Auto Scheduled") &
                    (sub_tasks_all["Start"].notna()) &
                    (sub_tasks_all["Finish"].notna()) &
                    (sub_tasks_all["Start"] <= now) &
                    (sub_tasks_all["Finish"] > now)
                ]
                st_autorefresh(interval=1000, key="subtask_countdown")
                if not sub_tasks_current.empty:
                    current_sub = sub_tasks_current.iloc[0]
                    time_remaining = current_sub["Finish"] - now
                    days = time_remaining.days
                    hours, remainder = divmod(time_remaining.seconds, 3600)
                    minutes, seconds = divmod(remainder, 60)
                    # Larger countdown text
                    st.markdown(f"<h2 style='color:#e0e0e0;'>Current Task '{current_sub['Name']}' ends in: {days}d {hours:02d}h:{minutes:02d}m:{seconds:02d}s</h2>", unsafe_allow_html=True)
                else:
                    st.markdown("<h2 style='color:#e0e0e0;'>No active task. Countdown for next sub-task:</h2>", unsafe_allow_html=True)
                    sub_tasks_future = sub_tasks_all[
                        (sub_tasks_all["Task Mode"] == "Auto Scheduled") &
                        (sub_tasks_all["Start"].notna())
                    ]
                    if not sub_tasks_future.empty:
                        next_sub = sub_tasks_future.iloc[0]
                        time_until_start = next_sub["Start"] - now
                        if time_until_start.total_seconds() < 0:
                            countdown_text = "Sub-task already started."
                        else:
                            days = time_until_start.days
                            hours, remainder = divmod(time_until_start.seconds, 3600)
                            minutes, seconds = divmod(remainder, 60)
                            countdown_text = f"Next Sub-Task '{next_sub['Name']}' starts in: {days}d {hours:02d}h:{minutes:02d}m:{seconds:02d}s"
                        st.markdown(f"<h2 style='color:#e0e0e0;'>{countdown_text}</h2>", unsafe_allow_html=True)
                # End of live countdown

                st.subheader(f"Auto Scheduled Tasks for {project_choice}")
                proj_indices = df.index[(df["Name"] == project_choice) & (df["Task Mode"] == "Manually Scheduled")].tolist()
                sel_idx = proj_indices[0]
                next_idx_list = df.index[(df["Task Mode"] == "Manually Scheduled") & (df.index > sel_idx)].tolist()
                next_idx = next_idx_list[0] if next_idx_list else len(df)
                sub_tasks = df.iloc[sel_idx+1 : next_idx]
                sub_tasks = sub_tasks[sub_tasks["Task Mode"] == "Auto Scheduled"].copy()
                if sub_tasks.empty:
                    st.info("No auto scheduled sub-tasks.")
                else:
                    st.dataframe(sub_tasks)
                    if {"Name", "Start", "Finish", "Label"}.issubset(sub_tasks.columns):
                        sub_tasks["sort_order"] = 1
                        sub_tasks.loc[sub_tasks["Task Mode"] == "Manually Scheduled", "sort_order"] = 0
                        sub_tasks = sub_tasks.sort_values(by=["sort_order", "Name"], ascending=[True, True])
                        sub_color_map = {nm: task_color(nm) for nm in sub_tasks["Name"].unique()}
                        sub_fig = px.timeline(
                            sub_tasks,
                            x_start="Start",
                            x_end="Finish",
                            y="Name",
                            color="Name",
                            color_discrete_map=sub_color_map,
                            title=f"Gantt Chart - Sub-Tasks of {project_choice}",
                            template="plotly_dark",
                            text="Label"
                        )
                        sub_fig.update_traces(
                            marker_line_color="white",
                            marker_line_width=1,
                            textposition="inside",
                            textfont_color="white"
                        )
                        sub_fig.update_yaxes(autorange="reversed")
                        sub_fig.update_layout(
                            font_color="white",
                            paper_bgcolor="#121212",
                            plot_bgcolor="#121212",
                            legend_font_color="white"
                        )
                        sub_fig.update_xaxes(color="white")
                        sub_fig.update_yaxes(color="white")
                        st.plotly_chart(sub_fig, use_container_width=True)
