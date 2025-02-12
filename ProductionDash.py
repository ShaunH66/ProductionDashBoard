import os
import re
import streamlit as st
import pandas as pd
import plotly.express as px

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
</style>
"""

st.set_page_config(page_title="Engineering Project Dashboard", layout="wide")
st.markdown(DARK_CSS, unsafe_allow_html=True)

# -----------------------------------------
# Excel File Path & Data Loading
# -----------------------------------------
EXCEL_PATH = r"Q:\MSE - Projects\Project Status\Production.xlsx"
LOGO_PATH = r"Q:\MSE - Projects\Project Status\Marchant Schmidt Logo.jpg"

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
def load_data(path: str) -> pd.DataFrame:
    df = pd.read_excel(path)

    # Normalize Name
    if "Name" in df.columns:
        df["Name"] = df["Name"].astype(str).str.strip().fillna("")
        df["Name"] = df["Name"].replace({
            r"(?i)^\s*fat\s*$": "FAT",
            r"(?i)^\s*shipping\s*$": "Shipping"
        }, regex=True)

    # Convert Start/Finish to datetime
    if "Start" in df.columns:
        df["Start"] = pd.to_datetime(df["Start"], errors="coerce")
    if "Finish" in df.columns:
        df["Finish"] = pd.to_datetime(df["Finish"], errors="coerce")

    df = df.reset_index(drop=True)

    # Extend zero-day FAT/Shipping to 3 days
    if {"Name", "Start", "Finish"}.issubset(df.columns):
        mask_fs = df["Name"].isin(["FAT", "Shipping"]) & df["Start"].notna() & df["Finish"].notna()
        durations = (df["Finish"] - df["Start"]).dt.days
        zero_mask = mask_fs & (durations == 0)
        df.loc[zero_mask, "Finish"] = df.loc[zero_mask, "Finish"] + pd.Timedelta(days=3)

    # Tag each row with the current "Project"
    df["Project"] = None
    current_project = None
    if "Task Mode" in df.columns and "Name" in df.columns:
        for i in df.index:
            if df.loc[i, "Task Mode"] == "Manually Scheduled":
                current_project = df.loc[i, "Name"]
            df.loc[i, "Project"] = current_project

    # Label = "Name (Project)"
    def make_label(row):
        if pd.notnull(row["Project"]):
            return f"{row['Name']} ({row['Project']})"
        return row["Name"]
    df["Label"] = df.apply(make_label, axis=1)

    # Parse "Duration" column to numeric
    if "Duration" in df.columns:
        df["Duration"] = df["Duration"].apply(parse_duration)

    return df

if not os.path.exists(EXCEL_PATH):
    st.error(f"Could not find Excel file at: {EXCEL_PATH}")
    st.stop()

df = load_data(EXCEL_PATH)

page = st.sidebar.radio("Navigation", ["Overall Dashboard", "Project Details"])

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
            # same sub-task name, different project
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

def build_color_map(data: pd.DataFrame) -> dict:
    cmap = {}
    for nm in data["Name"].unique():
        cmap[nm] = task_color(nm)
    return cmap

# -----------------------------------------
# PAGE 1: OVERALL DASHBOARD
# -----------------------------------------
if page == "Overall Dashboard":
    # Attempt to display company logo
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, width=350)
    else:
        st.warning(f"Logo not found: {LOGO_PATH}")

    st.title("Engineering Project Dashboard")

    st.subheader("Summary Metrics")
    c1, c2, c3, c4, c5 = st.columns(5)

    # 1) Total Projects (manually scheduled)
    total_projects = 0
    if "Task Mode" in df.columns:
        total_projects = (df["Task Mode"] == "Manually Scheduled").sum()
    with c1:
        st.metric("Total Projects", total_projects)

    # 2) Total Project Duration from manually scheduled
    if "Duration" in df.columns:
        ms_df = df[df["Task Mode"] == "Manually Scheduled"].copy()
        ms_df["Duration"] = pd.to_numeric(ms_df["Duration"], errors="coerce")
        ms_df = ms_df.dropna(subset=["Duration"])
        total_proj_duration = ms_df["Duration"].sum()
        with c2:
            st.metric("Total Project Duration (days)", f"{total_proj_duration:.0f}")

    # 3) Average Duration (all rows)
    if "Duration" in df.columns and pd.api.types.is_numeric_dtype(df["Duration"]):
        avg_duration = df["Duration"].mean()
        with c3:
            st.metric("Avg. Duration (days)", f"{avg_duration:.2f}")

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

    # Overall Project Duration Chart
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
    st.subheader("Overall Gantt Chart")

    if {"Name", "Start", "Finish", "Label"}.issubset(df.columns):
        gantt_df = df.dropna(subset=["Start", "Finish"]).copy()
        gantt_df["sort_order"] = 1
        if "Task Mode" in gantt_df.columns:
            gantt_df.loc[gantt_df["Task Mode"] == "Manually Scheduled", "sort_order"] = 0
        gantt_df = gantt_df.sort_values(by=["sort_order", "Name"], ascending=[True, True])

        color_map = build_color_map(gantt_df)
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

        st.subheader("Overlapping Engineering Tasks Across Projects")
        overlap_df = find_overlaps_subtasks(gantt_df)
        if overlap_df.empty:
            st.info("No Overlapping Engineering Tasks Across Projects")
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
            st.info("No manually scheduled projects found.")
        else:
            # Using a radio button instead of selectbox
            st.markdown("Pick a Project:")
            project_choice = st.radio("", manual_df["Name"].unique())

            proj_indices = df.index[(df["Name"] == project_choice) & (df["Task Mode"] == "Manually Scheduled")].tolist()
            if not proj_indices:
                st.warning("Project not found.")
            else:
                sel_idx = proj_indices[0]
                next_idx_list = df.index[(df["Task Mode"] == "Manually Scheduled") & (df.index > sel_idx)].tolist()
                next_idx = next_idx_list[0] if next_idx_list else len(df)
                sub_tasks = df.iloc[sel_idx+1 : next_idx]
                sub_tasks = sub_tasks[sub_tasks["Task Mode"] == "Auto Scheduled"].copy()

                st.subheader(f"Engineering Tasks for {project_choice}")
                if sub_tasks.empty:
                    st.info("No Engineering Tasks tasks.")
                else:
                    st.dataframe(sub_tasks)
                    if {"Name", "Start", "Finish", "Label"}.issubset(sub_tasks.columns):
                        sub_tasks["sort_order"] = 1
                        sub_tasks.loc[sub_tasks["Task Mode"] == "Manually Scheduled", "sort_order"] = 0
                        sub_tasks = sub_tasks.sort_values(by=["sort_order", "Name"], ascending=[True, True])

                        sub_color_map = build_color_map(sub_tasks)
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