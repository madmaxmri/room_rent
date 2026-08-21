import base64
from datetime import datetime, timezone
from io import BytesIO

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

from db import list_all_listings, list_recent_footprints, storage_label


sns.set_theme(style="whitegrid")


def _image_from_plot():
    buffer = BytesIO()
    plt.tight_layout()
    plt.savefig(buffer, format="png", dpi=140, bbox_inches="tight")
    plt.close()
    buffer.seek(0)
    return "data:image/png;base64," + base64.b64encode(buffer.read()).decode("ascii")


def _empty_chart(title, message):
    plt.figure(figsize=(7, 3.6))
    plt.axis("off")
    plt.text(0.5, 0.5, message, ha="center", va="center", fontsize=12)
    return {
        "title": title,
        "alt": message,
        "image": _image_from_plot(),
    }


def _json_safe(value):
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value


def _records(frame):
    return [
        {key: _json_safe(value) for key, value in row.items()}
        for row in frame.to_dict(orient="records")
    ]


def _listing_frame():
    frame = pd.DataFrame(list_all_listings(include_unavailable=True))
    if frame.empty:
        return frame

    for column in ["monthly_rent", "deposit", "bedrooms", "bathrooms", "area_sqft"]:
        if column not in frame:
            frame[column] = 0
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0)
    for column in ["city", "property_type", "furnished_status", "neighborhood"]:
        if column not in frame:
            frame[column] = "Unknown"
        frame[column] = frame[column].fillna("Unknown").astype(str)
    if "is_available" not in frame:
        frame["is_available"] = True
    frame["is_available"] = frame["is_available"].fillna(True).astype(bool)
    return frame


def _footprint_frame():
    frame = pd.DataFrame(list_recent_footprints(limit=1000))
    if frame.empty:
        return frame

    frame["event_type"] = frame.get("event_type", "activity").fillna("activity").astype(str)
    frame["created_at"] = pd.to_datetime(frame.get("created_at"), errors="coerce")
    return frame


def _rent_by_city(frame):
    if frame.empty:
        return _empty_chart("Average Rent by City", "No listing rent data available.")

    plot_data = (
        frame.groupby("city", as_index=False)["monthly_rent"]
        .mean()
        .sort_values("monthly_rent", ascending=False)
    )
    plt.figure(figsize=(7.6, 3.8))
    axis = sns.barplot(data=plot_data, x="city", y="monthly_rent", color="#1f6f78")
    axis.set_title("Average Monthly Rent by City")
    axis.set_xlabel("City")
    axis.set_ylabel("Average monthly rent")
    axis.tick_params(axis="x", rotation=25)
    return {
        "title": "Average Rent by City",
        "alt": "Bar chart of average monthly rent grouped by city.",
        "image": _image_from_plot(),
    }


def _property_type_distribution(frame):
    if frame.empty:
        return _empty_chart("Property Type Distribution", "No property type data available.")

    plt.figure(figsize=(7.6, 3.8))
    axis = sns.countplot(
        data=frame,
        x="property_type",
        order=frame["property_type"].value_counts().index,
        color="#5f8f9c",
    )
    axis.set_title("Listings by Property Type")
    axis.set_xlabel("Property type")
    axis.set_ylabel("Listings")
    axis.tick_params(axis="x", rotation=20)
    return {
        "title": "Property Type Distribution",
        "alt": "Bar chart of listing counts by property type.",
        "image": _image_from_plot(),
    }


def _rent_by_bedrooms(frame):
    if frame.empty:
        return _empty_chart("Rent by Bedrooms", "No bedroom rent data available.")

    plt.figure(figsize=(7.6, 3.8))
    axis = sns.boxplot(data=frame, x="bedrooms", y="monthly_rent", color="#b8d8dd")
    axis.set_title("Monthly Rent by Bedroom Count")
    axis.set_xlabel("Bedrooms")
    axis.set_ylabel("Monthly rent")
    return {
        "title": "Rent by Bedrooms",
        "alt": "Box plot of monthly rent grouped by bedroom count.",
        "image": _image_from_plot(),
    }


def _footprint_events(frame):
    if frame.empty:
        return _empty_chart("User Footprint Events", "No user footprint data available.")

    plt.figure(figsize=(7.6, 3.8))
    order = frame["event_type"].value_counts().index
    axis = sns.countplot(data=frame, y="event_type", order=order, color="#1f6f78")
    axis.set_title("User Footprints by Event")
    axis.set_xlabel("Events")
    axis.set_ylabel("Event type")
    return {
        "title": "User Footprint Events",
        "alt": "Horizontal bar chart of user footprint events by type.",
        "image": _image_from_plot(),
    }


def admin_analytics_payload():
    listings = _listing_frame()
    footprints = _footprint_frame()

    if listings.empty:
        listing_summary = {
            "listing_count": 0,
            "average_rent": 0,
            "available_count": 0,
            "average_area_sqft": 0,
        }
    else:
        listing_summary = {
            "listing_count": int(len(listings)),
            "average_rent": round(float(listings["monthly_rent"].mean()), 2),
            "available_count": int(listings["is_available"].sum()),
            "average_area_sqft": round(float(listings["area_sqft"].mean()), 2),
        }

    charts = [
        _rent_by_city(listings),
        _property_type_distribution(listings),
        _rent_by_bedrooms(listings),
        _footprint_events(footprints),
    ]

    top_cities = []
    if not listings.empty:
        top_cities = _records(
            listings.groupby("city", as_index=False)
            .agg(listings=("id", "count"), average_rent=("monthly_rent", "mean"))
            .sort_values(["listings", "average_rent"], ascending=[False, False])
            .head(5)
            .round({"average_rent": 2})
        )

    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "storage_label": storage_label(),
        "summary": listing_summary,
        "charts": charts,
        "top_cities": top_cities,
        "footprint_count": int(len(footprints)),
    }
