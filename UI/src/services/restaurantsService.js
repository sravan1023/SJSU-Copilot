const SJSU_COORDS = {
  lat: 37.3352,
  lon: -121.8811,
};

function distanceInMiles(lat1, lon1, lat2, lon2) {
  const toRad = (deg) => (deg * Math.PI) / 180;
  const R = 3958.8;

  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);

  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) *
      Math.cos(toRad(lat2)) *
      Math.sin(dLon / 2) ** 2;

  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function normalizeRestaurant(place) {
  const lat = place.lat ?? place.center?.lat;
  const lon = place.lon ?? place.center?.lon;

  if (!lat || !lon) return null;

  const tags = place.tags || {};
  const distance = distanceInMiles(SJSU_COORDS.lat, SJSU_COORDS.lon, lat, lon);

  return {
    id: `${place.type}-${place.id}`,
    name: tags.name || "Unnamed restaurant",
    cuisine: tags.cuisine
      ? tags.cuisine
          .split(";")
          .map((x) => x.trim())
          .filter(Boolean)
          .join(", ")
      : "Restaurant",
    address:
      [tags["addr:housenumber"], tags["addr:street"]]
        .filter(Boolean)
        .join(" ") || "Address not listed",
    lat,
    lon,
    distance,
  };
}

export async function fetchNearbyRestaurants() {
  const query = `
    [out:json][timeout:25];
    (
      node["amenity"="restaurant"](around:1000,${SJSU_COORDS.lat},${SJSU_COORDS.lon});
      way["amenity"="restaurant"](around:1000,${SJSU_COORDS.lat},${SJSU_COORDS.lon});
      relation["amenity"="restaurant"](around:1000,${SJSU_COORDS.lat},${SJSU_COORDS.lon});
    );
    out center tags;
  `;

  let response;

  try {
    response = await fetch("https://overpass-api.de/api/interpreter", {
      method: "POST",
      headers: {
        "Content-Type": "text/plain",
      },
      body: query,
    });
  } catch {
    throw new Error("Network error while fetching restaurants");
  }

  if (!response.ok) {
    throw new Error(`API error: ${response.status}`);
  }

  const data = await response.json();

  return (data.elements || [])
    .map(normalizeRestaurant)
    .filter(Boolean)
    .sort((a, b) => a.distance - b.distance)
    .slice(0, 8);
}

export function formatRestaurantsMessage(restaurants) {
  if (!restaurants.length) {
    return `## 🍽️ Nearby Restaurants to SJSU

I couldn’t find restaurants right now. Please try again in a moment.`;
  }

  const lines = restaurants.map((r, index) => {
    const mapUrl = `https://www.google.com/maps/search/?api=1&query=${r.lat},${r.lon}`;

    return `### ${index + 1}. ${r.name}

📍 ${r.address}  
🍴 ${r.cuisine}  
📏 ${r.distance.toFixed(2)} miles away  
👉 [Open in Maps](${mapUrl})`;
  });

  return `## 🍽️ Nearby Restaurants to SJSU

Here are some great spots near campus:

${lines.join("\n\n---\n\n")}

---

💡 Try asking:
- "cheap food near SJSU"
- "best pizza near campus"
- "late night food near SJSU"`;
}