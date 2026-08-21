from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.neighbors import NearestNeighbors
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
import pandas as pd

from db import list_all_listings


NUMERIC_COLUMNS = [
    "monthly_rent",
    "deposit",
    "bedrooms",
    "bathrooms",
    "area_sqft",
]

CATEGORY_COLUMNS = [
    "city",
    "neighborhood",
    "property_type",
    "furnished_status",
]

TEXT_COLUMNS = [
    "title",
    "city",
    "neighborhood",
    "property_type",
    "furnished_status",
    "description",
    "amenities",
]


def _listing_frame():
    listings = list_all_listings(include_unavailable=False)
    frame = pd.DataFrame(listings)
    if frame.empty:
        return frame

    for column in NUMERIC_COLUMNS:
        frame[column] = pd.to_numeric(frame.get(column, 0), errors="coerce").fillna(0)
    for column in CATEGORY_COLUMNS + TEXT_COLUMNS:
        frame[column] = frame.get(column, "").fillna("").astype(str)

    frame["text_features"] = frame[TEXT_COLUMNS].agg(" ".join, axis=1)
    return frame


def _train_recommender(frame):
    transformer = ColumnTransformer(
        transformers=[
            (
                "text",
                TfidfVectorizer(stop_words="english", ngram_range=(1, 2)),
                "text_features",
            ),
            (
                "category",
                OneHotEncoder(handle_unknown="ignore"),
                CATEGORY_COLUMNS,
            ),
            ("numeric", StandardScaler(), NUMERIC_COLUMNS),
        ],
        remainder="drop",
    )

    features = transformer.fit_transform(frame)
    model = NearestNeighbors(metric="cosine", algorithm="brute")
    model.fit(features)
    return Pipeline([("features", transformer), ("model", model)]), features, model


def _json_safe(value):
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value


def _listing_payload(row):
    item = row.drop(labels=["text_features"], errors="ignore").to_dict()
    return {key: _json_safe(value) for key, value in item.items()}


def recommended_listings(listing_id=None, limit=3):
    frame = _listing_frame()
    if frame.empty:
        return {"recommendations": [], "model": "scikit-learn NearestNeighbors"}

    limit = max(1, min(int(limit or 3), 8))
    if len(frame) == 1:
        only_listing = _listing_payload(frame.iloc[0])
        return {
            "recommendations": [only_listing],
            "model": "scikit-learn NearestNeighbors",
            "trained_on": len(frame),
        }

    _, features, model = _train_recommender(frame)

    if listing_id is not None and listing_id in set(frame["id"].tolist()):
        query_index = frame.index[frame["id"] == listing_id][0]
    else:
        query_index = 0

    neighbor_count = min(len(frame), limit + 1)
    distances, indices = model.kneighbors(features[query_index], n_neighbors=neighbor_count)

    recommendations = []
    for distance, index in zip(distances[0], indices[0]):
        row = frame.iloc[index]
        if listing_id is not None and int(row["id"]) == int(listing_id):
            continue
        item = _listing_payload(row)
        item["similarity_score"] = round(max(0, 1 - float(distance)), 4)
        recommendations.append(item)
        if len(recommendations) >= limit:
            break

    return {
        "recommendations": recommendations,
        "model": "scikit-learn NearestNeighbors",
        "trained_on": len(frame),
        "source_listing_id": listing_id,
    }
