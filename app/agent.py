# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import base64
import datetime
import json
import os
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors import AgentEngineSandboxCodeExecutor
from google.adk.memory import VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage
from google.genai import types

from .a2ui_utils import a2ui_callback

# Hardcoded GCP Project ID, Bucket Name, and Memory Bank ID (required on Agent Platform)
PROJECT_ID = "qwiklabs-gcp-03-c355417df227"
BUCKET_NAME = "travel-concierge-media-c355417df227"
MEMORY_BANK_ID = "1097037726613504000"


# WRITE: after each turn, send the session to Memory Bank for extraction.
async def generate_memories_callback(callback_context: CallbackContext):
    await callback_context.add_session_to_memory()
    return None


# Memory service builder for deployment
def memory_bank_service_builder():
    return VertexAiMemoryBankService(
        project=PROJECT_ID,
        location="us-east1",
        agent_engine_id=MEMORY_BANK_ID,
    )

# Initialize AgentEngineSandboxCodeExecutor using deployment_metadata.json if available
code_executor = None
metadata_path = os.path.join(os.path.dirname(__file__), "..", "deployment_metadata.json")
if os.path.exists(metadata_path):
    try:
        with open(metadata_path, "r") as f:
            meta = json.load(f)
            agent_engine_id = meta.get("remote_agent_runtime_id")
            if agent_engine_id:
                code_executor = AgentEngineSandboxCodeExecutor(agent_engine_resource_name=agent_engine_id)
    except Exception:
        pass

if code_executor is None:
    code_executor = AgentEngineSandboxCodeExecutor()


def get_destinations(city: str = "", category: str = "") -> str:
    """Reads travel destinations from the Firestore database.

    Args:
        city: Optional city name to filter by (e.g. 'Paris', 'Tokyo', 'New York').
        category: Optional category to filter by (e.g. 'Landmark', 'Museum', 'Nature').

    Returns:
        A list of matching travel destination recommendations.
    """
    db = firestore.Client(project=PROJECT_ID)
    collection_ref = db.collection("destinations")
    docs = collection_ref.stream()

    results = []
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        
        # Apply optional filtering
        match_city = not city or city.lower() in data.get("city", "").lower()
        match_cat = not category or category.lower() in data.get("category", "").lower()
        
        if match_city and match_cat:
            results.append(data)

    if not results:
        return f"No destinations found in Firestore matching city='{city}' category='{category}'."

    output = []
    for d in results:
        tags_str = ", ".join(d.get("tags", []))
        output.append(
            f"• {d.get('name')} ({d.get('city')}, {d.get('country')}) - Rating: {d.get('rating', 'N/A')}/5\n"
            f"  Category: {d.get('category')}\n"
            f"  Description: {d.get('description')}\n"
            f"  Tags: {tags_str}"
        )
    return "\n\n".join(output)


def add_destination(
    name: str,
    city: str,
    country: str,
    category: str,
    description: str,
    rating: float = 4.5,
    tags: str = "",
) -> str:
    """Adds a new travel destination recommendation to the Firestore database.

    Args:
        name: Name of the attraction or landmark (e.g. 'Statue of Liberty').
        city: City where it is located (e.g. 'New York').
        country: Country where it is located (e.g. 'USA').
        category: Category of attraction (e.g. 'Landmark', 'Museum', 'Park').
        description: Short summary of what makes this destination special.
        rating: Rating out of 5.0 (default 4.5).
        tags: Comma-separated tags (e.g. 'landmark, scenic, iconic').

    Returns:
        Confirmation message with the created document ID.
    """
    db = firestore.Client(project=PROJECT_ID)
    slug = f"{city.lower().replace(' ', '')}-{name.lower().replace(' ', '-')}"
    doc_id = "".join(c for c in slug if c.isalnum() or c == '-')

    doc_data = {
        "name": name,
        "city": city,
        "country": country,
        "category": category,
        "description": description,
        "rating": float(rating),
        "tags": [t.strip().lower() for t in tags.split(",") if t.strip()],
    }

    db.collection("destinations").document(doc_id).set(doc_data)
    return f"Successfully added destination '{name}' to Firestore with ID '{doc_id}'!"


def calculate_trip_budget(
    flights_usd: float,
    lodging_per_night_usd: float,
    nights: int,
    daily_expenses_usd: float,
    target_currency: str = "EUR",
) -> str:
    """Calculates an itemized trip budget total and converts it to a target currency.

    Args:
        flights_usd: Total estimated cost of flights in USD.
        lodging_per_night_usd: Accommodation cost per night in USD.
        nights: Total number of nights of accommodation.
        daily_expenses_usd: Daily allowance for food, local transport, and activities in USD.
        target_currency: Target currency code for conversion (e.g. 'EUR', 'JPY', 'GBP', 'CAD').

    Returns:
        An itemized breakdown of estimated trip costs in USD and target currency.
    """
    lodging_total = lodging_per_night_usd * nights
    days = max(1, nights)
    expenses_total = daily_expenses_usd * days
    total_usd = flights_usd + lodging_total + expenses_total

    rates = {
        "USD": 1.0,
        "EUR": 0.92,
        "GBP": 0.78,
        "JPY": 155.0,
        "CAD": 1.38,
        "AUD": 1.52,
        "CHF": 0.89,
    }
    rate = rates.get(target_currency.upper(), 1.0)
    total_converted = total_usd * rate
    currency_symbol = "€" if target_currency.upper() == "EUR" else ("¥" if target_currency.upper() == "JPY" else ("£" if target_currency.upper() == "GBP" else target_currency.upper()))

    return (
        f"Trip Budget Breakdown ({nights} nights / {days} days):\n"
        f"• Flights: ${flights_usd:,.2f}\n"
        f"• Accommodation ({nights} nights @ ${lodging_per_night_usd:,.2f}/night): ${lodging_total:,.2f}\n"
        f"• Daily Expenses ({days} days @ ${daily_expenses_usd:,.2f}/day): ${expenses_total:,.2f}\n"
        f"----------------------------------------\n"
        f"Total (USD): ${total_usd:,.2f}\n"
        f"Total ({target_currency.upper()}): {currency_symbol}{total_converted:,.2f}"
    )


def get_live_destination_info(destination_name: str) -> str:
    """Fetches real live destination metadata and current weather forecast using Open-Meteo public API.

    Args:
        destination_name: Name of the city or destination to look up (e.g. 'Paris', 'Tokyo', 'Rome').

    Returns:
        Real-time destination information including coordinates, country, timezone, population, and live weather forecast.
    """
    try:
        api_key = os.getenv("OPEN_METEO_API_KEY", "")
        encoded_name = urllib.parse.quote(destination_name)
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={encoded_name}&count=1&language=en&format=json"
        if api_key:
            geo_url += f"&apikey={api_key}"

        req = urllib.request.Request(geo_url, headers={"User-Agent": "TravelConcierge/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            geo_data = json.loads(resp.read().decode())

        if not geo_data.get("results"):
            return f"No live destination data found for '{destination_name}'."

        place = geo_data["results"][0]
        name = place.get("name", destination_name)
        country = place.get("country", "N/A")
        lat = place.get("latitude")
        lon = place.get("longitude")
        timezone = place.get("timezone", "N/A")
        population = place.get("population", "N/A")

        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
        if api_key:
            weather_url += f"&apikey={api_key}"

        req_w = urllib.request.Request(weather_url, headers={"User-Agent": "TravelConcierge/1.0"})
        with urllib.request.urlopen(req_w, timeout=5) as resp_w:
            weather_data = json.loads(resp_w.read().decode())

        cw = weather_data.get("current_weather", {})
        temp_c = cw.get("temperature", "N/A")
        wind_speed = cw.get("windspeed", "N/A")

        pop_fmt = f"{population:,}" if isinstance(population, (int, float)) else str(population)
        temp_f = f"{round(temp_c * 9/5 + 32, 1)}°F" if isinstance(temp_c, (int, float)) else "N/A"

        return (
            f"Live Destination Info for {name} ({country}):\n"
            f"• Coordinates: {lat}, {lon}\n"
            f"• Timezone: {timezone}\n"
            f"• Estimated Population: {pop_fmt}\n"
            f"• Current Weather: {temp_c}°C ({temp_f})\n"
            f"• Wind Speed: {wind_speed} km/h"
        )
    except Exception as e:
        return f"Error fetching live destination info for '{destination_name}': {e}"


def geocode_address(address: str) -> str:
    """Converts an address or place name into coordinates (latitude, longitude) using Google Maps Geocoding API.

    Args:
        address: The address or place name to geocode (e.g. 'Eiffel Tower, Paris' or '1600 Amphitheatre Pkwy, Mountain View, CA').

    Returns:
        Formatted address, latitude, longitude, and place ID.
    """
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not api_key or api_key == "PASTE_KEY_HERE":
        return "Google Maps API Key (GOOGLE_MAPS_API_KEY) is missing or unconfigured in .env."

    try:
        encoded_address = urllib.parse.quote(address)
        url = f"https://maps.googleapis.com/maps/api/geocode/json?address={encoded_address}&key={api_key}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())

        if data.get("status") != "OK" or not data.get("results"):
            return f"Geocoding failed for '{address}'. Status: {data.get('status', 'UNKNOWN')}"

        result = data["results"][0]
        formatted_address = result.get("formatted_address", address)
        location = result.get("geometry", {}).get("location", {})
        lat = location.get("lat")
        lng = location.get("lng")
        place_id = result.get("place_id", "N/A")

        return (
            f"Geocoding Result for '{address}':\n"
            f"• Formatted Address: {formatted_address}\n"
            f"• Location: Latitude {lat}, Longitude {lng}\n"
            f"• Place ID: {place_id}"
        )
    except Exception as e:
        return f"Error calling Google Geocoding API for '{address}': {e}"


def find_nearby_places(
    latitude: float,
    longitude: float,
    place_type: str = "tourist_attraction",
    radius_meters: float = 1000.0,
) -> str:
    """Finds nearby places of a given type around coordinates using Google Places API (New) searchNearby REST endpoint.

    Args:
        latitude: Latitude coordinate for search center.
        longitude: Longitude coordinate for search center.
        place_type: Type of place to search for (e.g. 'restaurant', 'tourist_attraction', 'museum', 'park', 'cafe').
        radius_meters: Search radius in meters (default 1000.0).

    Returns:
        List of nearby places with name, formatted address, and location coordinates.
    """
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not api_key or api_key == "PASTE_KEY_HERE":
        return "Google Maps API Key (GOOGLE_MAPS_API_KEY) is missing or unconfigured in .env."

    try:
        url = "https://places.googleapis.com/v1/places:searchNearby"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location",
        }
        body = {
            "includedTypes": [place_type],
            "locationRestriction": {
                "circle": {
                    "center": {
                        "latitude": float(latitude),
                        "longitude": float(longitude),
                    },
                    "radius": float(radius_meters),
                }
            },
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())

        places = data.get("places", [])
        if not places:
            return f"No nearby places found of type '{place_type}' within {radius_meters}m of ({latitude}, {longitude})."

        results = []
        for p in places:
            name = p.get("displayName", {}).get("text", "Unknown Place")
            addr = p.get("formattedAddress", "N/A")
            loc = p.get("location", {})
            p_lat = loc.get("latitude", "N/A")
            p_lng = loc.get("longitude", "N/A")
            results.append(
                f"• Name: {name}\n"
                f"  Address: {addr}\n"
                f"  Location: ({p_lat}, {p_lng})"
            )

        return f"Nearby '{place_type}' Places within {radius_meters}m:\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error calling Google Places API (New) for location ({latitude}, {longitude}): {e}"


def generate_destination_photo(
    destination_name: str,
    prompt: str = "",
    tool_context: ToolContext = None,
) -> str:
    """Generates an image for a travel destination or landmark using the gemini-3.1-flash-lite-image model, saves it as an artifact, and uploads it to Cloud Storage.

    Args:
        destination_name: Name of the destination or venue (e.g. 'Eiffel Tower', 'Shibuya Crossing').
        prompt: Optional visual details describing the scene (e.g. 'at sunset', 'in winter snow').
        tool_context: ADK ToolContext injected automatically by the runtime.

    Returns:
        The public HTTPS URL (https://storage.googleapis.com/<bucket>/<object>) of the generated image.
    """
    try:
        full_prompt = f"A high quality realistic photograph of {destination_name}"
        if prompt:
            full_prompt += f", {prompt}"

        # Initialize genai client in global region as requested
        genai_client = genai.Client(
            vertexai=True,
            project=PROJECT_ID,
            location="global",
        )

        response = genai_client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=full_prompt,
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
            ),
        )

        image_bytes = None
        if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.inline_data:
                    image_bytes = part.inline_data.data
                    break

        if not image_bytes:
            return f"Failed to generate image bytes for '{destination_name}'."

        clean_name = "".join(c for c in destination_name.lower().replace(" ", "_") if c.isalnum() or c == "_")
        filename = f"{clean_name}_{int(datetime.datetime.now().timestamp())}.jpg"

        # 1. Save artifact if tool_context is provided
        if tool_context is not None:
            artifact_part = types.Part.from_bytes(
                data=image_bytes,
                mime_type="image/jpeg",
            )
            tool_context.save_artifact(
                filename=filename,
                artifact=artifact_part,
            )

        # 2. Upload image bytes to public Cloud Storage bucket
        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(image_bytes, content_type="image/jpeg")

        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"
        return f"Successfully generated photo for '{destination_name}'. Public Image URL: {public_url}"

    except Exception as e:
        return f"Error generating photo for '{destination_name}': {e}"


def generate_destination_video(
    destination_name: str,
    prompt: str = "",
    tool_context: ToolContext = None,
) -> str:
    """Generates a short video preview for a travel destination or landmark using the gemini-omni-flash-preview model in the global region, saves it as an artifact, and uploads it to Cloud Storage.

    Args:
        destination_name: Name of the destination or venue (e.g. 'Shibuya Crossing', 'Eiffel Tower', 'Grand Canyon').
        prompt: Optional visual details describing the video scene (e.g. 'rainy street reflections at night', 'sunset aerial view').
        tool_context: ADK ToolContext injected automatically by the runtime.

    Returns:
        The public HTTPS URL (https://storage.googleapis.com/<bucket>/<object>) of the generated video.
    """
    try:
        full_prompt = f"A short cinematic 3-second video preview of {destination_name}"
        if prompt:
            full_prompt += f", {prompt}"

        genai_client = genai.Client(
            vertexai=True,
            project=PROJECT_ID,
            location="global",
        )

        res = genai_client.interactions.create(
            model="gemini-omni-flash-preview",
            input=full_prompt,
        )

        video_bytes = None
        if hasattr(res, "outputs") and res.outputs:
            for out in res.outputs:
                if hasattr(out, "data") and out.data:
                    vdata = out.data
                    video_bytes = base64.b64decode(vdata) if isinstance(vdata, str) else vdata
                    break
        elif hasattr(res, "output_video") and res.output_video and getattr(res.output_video, "data", None):
            vdata = res.output_video.data
            video_bytes = base64.b64decode(vdata) if isinstance(vdata, str) else vdata

        if not video_bytes:
            return f"Failed to generate video bytes for '{destination_name}'."

        clean_name = "".join(c for c in destination_name.lower().replace(" ", "_") if c.isalnum() or c == "_")
        filename = f"{clean_name}_{int(datetime.datetime.now().timestamp())}.mp4"

        # 1. Save artifact if tool_context is provided
        if tool_context is not None:
            artifact_part = types.Part.from_bytes(
                data=video_bytes,
                mime_type="video/mp4",
            )
            tool_context.save_artifact(
                filename=filename,
                artifact=artifact_part,
            )

        # 2. Upload video bytes to public Cloud Storage bucket
        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(video_bytes, content_type="video/mp4")

        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"
        return f"Successfully generated video for '{destination_name}'. Public Video URL: {public_url}"

    except Exception as e:
        return f"Error generating video for '{destination_name}': {e}"


def get_weather(query: str) -> str:
    """Simulates a web search. Use it get information on weather.

    Args:
        query: A string containing the location to get weather information for.

    Returns:
        A string with the simulated weather information for the queried location.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        return "It's 60 degrees and foggy."
    return "It's 90 degrees and sunny."


def get_current_time(query: str) -> str:
    """Simulates getting the current time for a city.

    Args:
        city: The name of the city to get the current time for.

    Returns:
        A string with the current time information.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        tz_identifier = "America/Los_Angeles"
    else:
        return f"Sorry, I don't have timezone information for query: {query}."

    tz = ZoneInfo(tz_identifier)
    now = datetime.datetime.now(tz)
    return f"The current time for query {query} is {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')}"


schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

a2ui_instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are Travel Concierge, a helpful AI assistant for trip planning, destination discovery, and travel recommendations. "
        "You remember user preferences, facts, and ALL user allergies (e.g. food allergies, dietary restrictions, environmental/medical allergies) across sessions using your Memory Bank. "
        "Pay strict attention whenever the user mentions any allergies or dietary constraints. Always remember them and ensure future travel recommendations, restaurant suggestions, and itineraries strictly accommodate and avoid those allergens. "
        "You can execute Python code in a safe Agent Engine sandbox environment when needed for complex calculations or processing."
    ),
    workflow_description=(
        "Analyze the user's travel request, use available tools (such as geocode_address, find_nearby_places, generate_destination_photo, generate_destination_video, get_live_destination_info, get_destinations, add_destination, calculate_trip_budget, get_weather, get_current_time), "
        "and return structured UI when appropriate."
    ),
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        "{\"Image\": {\"url\": {\"literalString\": \"https://...\"}}}. Never point an "
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-flash-latest",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    code_executor=code_executor,
    instruction=a2ui_instruction,
    tools=[
        PreloadMemoryTool(),
        generate_destination_photo,
        generate_destination_video,
        geocode_address,
        find_nearby_places,
        get_live_destination_info,
        get_destinations,
        add_destination,
        calculate_trip_budget,
        get_weather,
        get_current_time,
    ],
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)
