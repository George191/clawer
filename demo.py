from openepi_client import GeoLocation
from openepi_client.weather import AsyncWeatherClient
from openepi_client.geocoding import AsyncGeocodeClient


async def main():
    # Getting the sunrise and sunset times for a location
    sunrise_sunset = await AsyncWeatherClient.get_sunrise(geolocation=GeoLocation(lat=51.5074, lon=0.1278))
    print(sunrise_sunset)

    # Getting the weather forecast for a location
    forecast = await AsyncWeatherClient.get_location_forecast(geolocation=GeoLocation(lat=51.5074, lon=0.1278, alt=0))
    print(forecast)

    # Searching for coordinates for a location
    feature_collection = await AsyncGeocodeClient.geocode(q="Kigali, Rwanda")
    print(feature_collection)

    # Geocode with priority to a lat and lon
    feature_collection = await AsyncGeocodeClient.geocode(q="Kigali, Rwanda", geolocation=GeoLocation(lat=51.5074, lon=0.1278))
    print(feature_collection)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
