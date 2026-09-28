"""Script to seed initial destinations into GCP Firestore database."""

import os
from google.cloud import firestore

# Hardcode project ID to ensure compatibility across local dev and Agent Platform
PROJECT_ID = "qwiklabs-gcp-03-c355417df227"

SAMPLE_DESTINATIONS = [
    {
        "id": "paris-eiffel-tower",
        "name": "Eiffel Tower",
        "city": "Paris",
        "country": "France",
        "category": "Landmark",
        "rating": 4.8,
        "description": "Iconic iron lattice tower on the Champ de Mars with panoramic views and fine dining.",
        "tags": ["landmark", "romantic", "iconic", "views"],
    },
    {
        "id": "paris-louvre-museum",
        "name": "Louvre Museum",
        "city": "Paris",
        "country": "France",
        "category": "Museum",
        "rating": 4.9,
        "description": "World's largest art museum housing masterworks like the Mona Lisa and Venus de Milo.",
        "tags": ["art", "museum", "culture", "history"],
    },
    {
        "id": "tokyo-shibuya-crossing",
        "name": "Shibuya Crossing",
        "city": "Tokyo",
        "country": "Japan",
        "category": "Shopping & Nightlife",
        "rating": 4.7,
        "description": "Famous high-density pedestrian scramble intersection surrounded by neon billboards and shops.",
        "tags": ["shopping", "nightlife", "modern", "iconic"],
    },
    {
        "id": "kyoto-arashiyama-bamboo",
        "name": "Arashiyama Bamboo Grove",
        "city": "Kyoto",
        "country": "Japan",
        "category": "Nature",
        "rating": 4.9,
        "description": "Serene bamboo forest walking path leading to historic temples, gardens, and tea houses.",
        "tags": ["nature", "serene", "temples", "scenic"],
    },
    {
        "id": "newyork-central-park",
        "name": "Central Park",
        "city": "New York",
        "country": "USA",
        "category": "Park & Culture",
        "rating": 4.8,
        "description": "Sprawling 843-acre urban park featuring walking trails, boating lake, zoo, and conservatory garden.",
        "tags": ["park", "outdoors", "scenic", "family"],
    },
]


def seed_database():
    """Populates Firestore with initial destination records."""
    print(f"Connecting to Firestore with project ID: {PROJECT_ID}")
    try:
        import subprocess
        import google.oauth2.credentials
        token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
        creds = google.oauth2.credentials.Credentials(token)
        db = firestore.Client(project=PROJECT_ID, credentials=creds)
    except Exception:
        db = firestore.Client(project=PROJECT_ID)

    collection_ref = db.collection("destinations")

    print("Seeding destinations collection...")
    for item in SAMPLE_DESTINATIONS:
        doc_id = item["id"]
        doc_data = {k: v for k, v in item.items() if k != "id"}
        collection_ref.document(doc_id).set(doc_data)
        print(f"  ✓ Seeded document: destinations/{doc_id} ({doc_data['name']})")

    print("\n✅ Seeding complete!")


if __name__ == "__main__":
    seed_database()
