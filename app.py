import streamlit as st
import folium
from streamlit_folium import folium_static
import requests
import configparser
import polyline
import pandas as pd
from datetime import datetime, timedelta

# Initialize session state for all variables
if 'routes' not in st.session_state:
    st.session_state.routes = []
if 'selected_route_index' not in st.session_state:
    st.session_state.selected_route_index = 0
if 'departure_date' not in st.session_state:
    st.session_state.departure_date = datetime.now().date()
if 'departure_time' not in st.session_state:
    st.session_state.departure_time = datetime.now().time()
if 'destinations' not in st.session_state:
    st.session_state.destinations = []


def get_coords(place_name):
    """Geocode a place name to coordinates using Nominatim."""
    if not place_name: return None, None
    try:
        url = f"https://nominatim.openstreetmap.org/search?q={place_name}&format=json"
        headers = {'User-Agent': 'Aisin-Internship-CarNavApp/1.0 (anothay555@gmail.com)'}
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        if data:
            return float(data[0]['lat']), float(data[0]['lon'])
    except requests.exceptions.RequestException as e:
        st.error(f"Error calling Nominatim API: {e}")
    except (KeyError, IndexError):
        st.error(f"Could not find coordinates for '{place_name}'. Please try a different name.")
    return None, None

def get_route(coords_list, alternatives=False):
    """Get one or more routes from OSRM for a list of coordinates."""
    try:
        # Format coordinates into a semicolon-separated string of lon,lat
        coords_str = ";".join([f"{lon},{lat}" for lon, lat in coords_list])
        url = f"http://router.project-osrm.org/route/v1/driving/{coords_str}?overview=full&steps=true&alternatives={str(alternatives).lower()}"
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        if data.get('routes'):
            return data['routes']
    except requests.exceptions.RequestException as e:
        st.error(f"Error calling OSRM API: {e}")
    except (KeyError, IndexError):
        st.error("Could not find a route. Please check the coordinates.")
    return None

def get_fuel_stations_along_route(route_geometry, radius_meters=5000):
    """Get fuel stations within a certain radius of a route polyline using Overpass API."""
    try:
        points = polyline.decode(route_geometry)
        if not points:
            st.warning("Route geometry is empty, cannot search for fuel stations.")
            return []

        MAX_QUERY_POINTS = 50
        if len(points) > MAX_QUERY_POINTS:
            step = len(points) // MAX_QUERY_POINTS
            points = points[::step]

        points_str = ", ".join([f"{p[0]}, {p[1]}" for p in points])
        overpass_url = "https://overpass-api.de/api/interpreter"
        overpass_query = f"""
        [out:json][timeout:60];
        (
          node["amenity"="fuel"](around:{radius_meters}, {points_str});
          way["amenity"="fuel"](around:{radius_meters}, {points_str});
          relation["amenity"="fuel"](around:{radius_meters}, {points_str});
        );
        out center;
        """
        response = requests.post(overpass_url, data={'data': overpass_query}, timeout=65)
        response.raise_for_status()
        data = response.json()
        st.info(f"Found {len(data.get('elements', []))} fuel stations within {radius_meters/1000}km of the route.")
        return data.get('elements', [])

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 504:
            st.warning("The map server is currently busy (Gateway Timeout). Please try again later.")
        else:
            st.error(f"Could not fetch fuel stations: {e.response.reason} ({e.response.status_code}).")
            st.code(e.response.text)
        return []
    except requests.exceptions.RequestException as e:
        st.warning(f"Could not fetch fuel stations due to a network issue: {e}")
        return []
    return []


def format_duration(seconds):
    """Format duration in seconds to a readable string (h m s)."""
    if not seconds: return "0s"
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    duration_str = ""
    if hours > 0: duration_str += f"{int(hours)}h "
    if minutes > 0: duration_str += f"{int(minutes)}m "
    if seconds > 0 or not duration_str: duration_str += f"{int(seconds)}s"
    return duration_str.strip()

def create_map(routes, coords_list, place_names, config, selected_index=0, fuel_stations=None):
    """Create a Folium map with waypoints and optional fuel station markers."""
    start_coords = coords_list[0]
    end_coords = coords_list[-1]
    map_center = [(start_coords[1] + end_coords[1]) / 2, (start_coords[0] + end_coords[0]) / 2]
    m = folium.Map(location=map_center, zoom_start=int(config.get('Map', 'zoom_start', fallback=10)))

    # Add marker for start point using the provided place name
    folium.Marker(location=[start_coords[1], start_coords[0]], popup=place_names[0], icon=folium.Icon(color='green', icon='car', prefix='fa')).add_to(m)
    # Add marker for end point using the provided place name
    folium.Marker(location=[end_coords[1], end_coords[0]], popup=place_names[-1], icon=folium.Icon(color='red', icon='flag-checkered', prefix='fa')).add_to(m)
    
    # Add markers for intermediate destinations (waypoints) using their names
    for i, (lon, lat) in enumerate(coords_list[1:-1]):
        popup_text = place_names[i + 1]
        folium.Marker(location=[lat, lon], popup=popup_text, icon=folium.Icon(color='blue', icon='map-marker', prefix='fa')).add_to(m)

    all_points = []
    for i, route in enumerate(routes):
        if route and 'geometry' in route:
            route_points = polyline.decode(route['geometry'])
            all_points.extend(route_points)
            
            if i == selected_index:
                line_options = {'color': config.get('Route', 'color', fallback='blue'), 'weight': config.getint('Route', 'weight', fallback=6), 'opacity': 0.9, 'popup': f"Selected Route: {format_duration(route.get('duration'))}"}
            else:
                line_options = {'color': '#888888', 'weight': 5, 'opacity': 0.7, 'popup': f"Alternative: {format_duration(route.get('duration'))}"}
            folium.PolyLine(locations=route_points, **line_options).add_to(m)

    if fuel_stations:
        for station in fuel_stations:
            lat = station.get('lat') or station.get('center', {}).get('lat')
            lon = station.get('lon') or station.get('center', {}).get('lon')
            if lat and lon:
                name = station.get('tags', {}).get('name', 'Fuel Station')
                folium.Marker(location=[lat, lon], popup=name, icon=folium.Icon(color='orange', icon='gas-pump', prefix='fa')).add_to(m)

    if all_points:
        m.fit_bounds([[min(p[0] for p in all_points), min(p[1] for p in all_points)],
                      [max(p[0] for p in all_points), max(p[1] for p in all_points)]])
    
    return m

def create_route_details_df(route):
    """Creates a pandas DataFrame from the route legs and steps."""
    route_details = []
    total_steps = 1
    for leg_index, leg in enumerate(route['legs']):
        for step in leg['steps']:
            maneuver = step.get('maneuver', {})
            maneuver_type = maneuver.get('type', 'unknown').replace('_', ' ').title()
            modifier = maneuver.get('modifier', '').replace('_', ' ').title()
            road_name = step.get('name', '')
            
            final_instruction = ""
            if maneuver_type == 'Depart':
                final_instruction = f"Depart on {road_name}" if road_name else "Depart"
            elif maneuver_type == 'New Name':
                final_instruction = f"Continue onto {road_name}"
            elif maneuver_type == 'Arrive' and leg_index < len(route['legs']) - 1:
                final_instruction = f"You have arrived at Waypoint {leg_index + 1}."
            elif maneuver_type == 'Arrive':
                final_instruction = maneuver.get('instruction', "You have arrived at your final destination.")
            else:
                instruction_parts = [maneuver_type, modifier]
                if road_name:
                    instruction_parts.append(f"onto {road_name}")
                final_instruction = " ".join(part for part in instruction_parts if part)

            route_details.append({
                "Step": total_steps,
                "Instruction": final_instruction,
                "Distance (km)": f"{step['distance'] / 1000:.2f}",
                "Time": format_duration(step['duration']),
            })
            total_steps += 1
            
    return pd.DataFrame(route_details)

def main():
    """Main function to run the Streamlit app."""
    st.set_page_config(page_title="Car Navigation", layout="wide")
    st.title("Car Navigation System")

    config = configparser.ConfigParser()
    try:
        config.read('config.ini')
    except configparser.Error:
        st.warning("config.ini file not found or contains errors. Using default values.")

    # --- Sidebar Inputs ---
    st.sidebar.header("Navigation Input")
    input_method = st.sidebar.radio("Input Method:", ("Place Name", "Coordinates"), horizontal=True)

    if input_method == "Place Name":
        start_place = st.sidebar.text_input("Start Location", config.get('Defaults', 'start_place', fallback="Tokyo Station"))
        
        # --- Waypoints UI ---
        st.sidebar.subheader("Waypoints")
        
        # Display and manage current destinations
        for i, dest in enumerate(st.session_state.destinations):
            col1, col2 = st.sidebar.columns([4, 1])
            col1.write(f"{i+1}. {dest}")
            if col2.button("🗑️", key=f"del_{i}", help="Remove this destination"):
                st.session_state.destinations.pop(i)
                st.rerun()

        # Initialize session state for toggling the input
        if 'show_add_destination_input' not in st.session_state:
            st.session_state.show_add_destination_input = False

        # If the input is hidden, show the '+ Add Destination' button
        if not st.session_state.show_add_destination_input:
            if st.sidebar.button("➕ Add Destination", use_container_width=True):
                st.session_state.show_add_destination_input = True
                st.rerun()
        else:
            # If the input is shown
            new_destination = st.sidebar.text_input("New destination name", key="new_dest_input_field", placeholder="e.g., Fukuoka Tower", label_visibility="collapsed")
            
            col_add, col_cancel = st.sidebar.columns(2)
            with col_add:
                if st.button("Add", use_container_width=True, key="confirm_add_dest"):
                    if new_destination:
                        st.session_state.destinations.append(new_destination)
                        # Hide the input field again after adding
                        st.session_state.show_add_destination_input = False
                        st.rerun()
                    else:
                        st.sidebar.warning("Please enter a destination.")
            
            with col_cancel:
                if st.button("Cancel", use_container_width=True, key="cancel_add_dest"):
                    st.session_state.show_add_destination_input = False
                    st.rerun()

        end_place = st.sidebar.text_input("End Location", config.get('Defaults', 'end_place', fallback="Shibuya Crossing"))

    else: # Coordinates - Waypoints not supported for coordinate input for simplicity
        start_place, end_place = None, None # Ensure these are None
        st.session_state.destinations = [] # Clear destinations if switching to coordinates
        col1, col2 = st.sidebar.columns(2)
        with col1:
            st.write("Start")
            start_lat = st.number_input("Lat", value=float(config.get('Defaults', 'start_lat', fallback=35.6812)), format="%.6f", key="start_lat")
            start_lon = st.number_input("Lon", value=float(config.get('Defaults', 'start_lon', fallback=139.7671)), format="%.6f", key="start_lon")
        with col2:
            st.write("End")
            end_lat = st.number_input("Lat", value=float(config.get('Defaults', 'end_lat', fallback=35.6595)), format="%.6f", key="end_lat")
            end_lon = st.number_input("Lon", value=float(config.get('Defaults', 'end_lon', fallback=139.7005)), format="%.6f", key="end_lon")

    st.sidebar.header("Options")
    show_alternatives = st.sidebar.checkbox("Show alternative routes", value=True)
    show_fuel = st.sidebar.checkbox("Show fuel stations", value=False)
    fuel_search_radius_km = 0
    if show_fuel:
        fuel_search_radius_km = st.sidebar.slider("Fuel Search Radius (km)", 1, 25, 5, 1, help="Search for fuel stations within this distance from the route.")

    st.sidebar.header("Trip Settings")
    col1, col2 = st.sidebar.columns(2)
    with col1: st.date_input("Departure Date", key='departure_date')
    with col2: st.time_input("Departure Time", key='departure_time')
    fuel_efficiency = st.sidebar.number_input("Fuel Efficiency (km/L)", 1.0, value=float(config.get('Vehicle', 'fuel_efficiency_km_l', fallback=15.0)), step=0.1)
    fuel_price = st.sidebar.number_input("Fuel Price (¥/L)", 100.0, value=float(config.get('Vehicle', 'fuel_price_yen_l', fallback=175.0)), step=1.0)

    if st.sidebar.button("Get Route", use_container_width=True):
        all_coords = []
        if input_method == "Place Name":
            places_to_geocode = [start_place] + st.session_state.destinations + [end_place]
            st.session_state.all_places = places_to_geocode # Save place names for map popups
            valid_trip = True
            for place in places_to_geocode:
                lat, lon = get_coords(place)
                if lat is not None and lon is not None:
                    all_coords.append((lon, lat))
                else:
                    st.sidebar.error(f"Could not find '{place}'. Route calculation cancelled.")
                    valid_trip = False
                    break
            if not valid_trip: all_coords = []
        else: # Coordinates
            all_coords = [(start_lon, start_lat), (end_lon, end_lat)]
            st.session_state.all_places = ["Start", "End"] # Generic names for coordinate mode

        if len(all_coords) >= 2:
            with st.spinner("Calculating routes..."):
                routes = get_route(all_coords, show_alternatives)
                if routes:
                    routes.sort(key=lambda r: r.get('duration', float('inf')))
                    st.session_state.routes = routes
                    st.session_state.selected_route_index = 0
                    st.session_state.all_coords = all_coords # Save coords for map
                else:
                    st.session_state.routes = []
                    st.error("No routes found. Please check your locations.")
        else:
            st.sidebar.warning("Please provide at least a start and end location.")


    if st.session_state.routes:
        routes = st.session_state.routes
        all_coords = st.session_state.all_coords
        all_places = st.session_state.all_places
        
        if len(routes) > 1:
            route_options = [f"Route {i+1}: {format_duration(r.get('duration'))} ({r.get('distance', 0)/1000:.1f} km)" for i, r in enumerate(routes)]
            st.session_state.selected_route_index = st.radio("Choose a route:", options=range(len(route_options)), format_func=lambda x: route_options[x], horizontal=True, index=st.session_state.selected_route_index)

        selected_route = routes[st.session_state.selected_route_index]
        
        duration_seconds = selected_route.get('duration', 0)
        distance_km = selected_route.get('distance', 0) / 1000
        departure_datetime = datetime.combine(st.session_state.departure_date, st.session_state.departure_time)
        eta = departure_datetime + timedelta(seconds=duration_seconds)
        fuel_needed = distance_km / fuel_efficiency if fuel_efficiency > 0 else 0
        estimated_cost = fuel_needed * fuel_price

        st.markdown("---")
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Route Summary")
            st.metric(label="⏱️ Duration", value=format_duration(duration_seconds))
            st.metric(label="📏 Distance", value=f"{distance_km:.2f} km")
        with col2:
            st.subheader("Trip Estimation")
            st.metric(label="📅 ETA", value=eta.strftime('%b %d, %H:%M'))
            st.metric(label="⛽ Fuel Needed", value=f"{fuel_needed:.2f} L")
            st.metric(label="💴 Estimated Cost", value=f"¥{estimated_cost:,.0f}")
        
        st.markdown("---")

        st.subheader("Navigation Map")
        fuel_stations_data = []
        if show_fuel:
            with st.spinner("Searching for fuel stations near your route..."):
                fuel_stations_data = get_fuel_stations_along_route(selected_route['geometry'], radius_meters=fuel_search_radius_km * 1000)

        folium_map = create_map(routes, all_coords, all_places, config, st.session_state.selected_route_index, fuel_stations=fuel_stations_data)
        folium_static(folium_map, width=1200, height=500)

        st.subheader("Route Details")
        df = create_route_details_df(selected_route)
        st.dataframe(df, use_container_width=True, hide_index=True)

if __name__ == "__main__":
    main()

