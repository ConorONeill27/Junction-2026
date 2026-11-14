"""Standalone, key-free restaurant reviewer. Run from the repository root."""
import json
import math
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="WorldGit Reviewer")


class Pose(BaseModel):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    heading: float = Field(ge=0, lt=360, allow_inf_nan=False)


class Settings(BaseModel):
    simulated: bool = False
    latitude: float = Field(default=60.1567426, ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(default=24.6304562, ge=-180, le=180, allow_inf_nan=False)
    heading: float = Field(default=0, ge=0, lt=360, allow_inf_nan=False)
    north_offset: float = Field(default=0, ge=-360, le=360, allow_inf_nan=False)


settings = Settings()


def visible_reviews(pose, places):
    matches = []
    lat1 = math.radians(pose.latitude)
    for place in places:
        lat2 = math.radians(place['coordinates']['latitude'])
        delta_lat = lat2 - lat1
        delta_lon = math.radians(place['coordinates']['longitude'] - pose.longitude)
        a = math.sin(delta_lat / 2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(delta_lon / 2)**2
        distance = 6371000 * 2 * math.asin(math.sqrt(min(1, max(0, a))))
        bearing = math.degrees(math.atan2(math.sin(delta_lon)*math.cos(lat2),
            math.cos(lat1)*math.sin(lat2)-math.sin(lat1)*math.cos(lat2)*math.cos(delta_lon))) % 360
        angle = (bearing - pose.heading + 180) % 360 - 180
        # At coincident coordinates no direction can be inferred.
        if 0.05 < distance <= 10 and abs(angle) <= 30:
            matches.append(dict(place, distance_m=round(distance, 2), bearing_deg=bearing, angle_deg=angle))
    return sorted(matches, key=lambda item: item['distance_m'])


@app.get('/')
def index():
    return FileResponse(ROOT / 'simulator.html')


@app.get('/api/places')
def places():
    return json.loads((ROOT / 'places.json').read_text(encoding='utf-8'))


@app.get('/api/settings')
def get_settings():
    return settings


@app.post('/api/settings')
def set_settings(value: Settings):
    global settings
    settings = value
    return settings


@app.post('/api/reviews')
def reviews(pose: Pose):
    config = settings
    if config.simulated:
        pose = Pose(latitude=config.latitude, longitude=config.longitude, heading=config.heading)
    else:
        pose = pose.model_copy(update={'heading': (pose.heading + config.north_offset) % 360})
    return {'reviews': visible_reviews(pose, places()), 'simulated': config.simulated}
