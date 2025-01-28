import grpc
import time
import os
import cv2
import numpy as np
import math
import threading
from concurrent import futures
import asyncio
import mentice.mentice.common.v1 as common
import mentice.mentice.fluoro.v1 as fluoro
import mentice.mentice.geometry.v1 as geometry
from utils import save_image
import json

OUTPUT_DIR = "fluoroscopy_images"
os.makedirs(OUTPUT_DIR, exist_ok=True)

from grpclib.client import Channel

channel = Channel(host="127.0.0.1", port=50051)
fluoro_service = fluoro.FluoroStub(channel)
geo_service = geometry.GeometryStub(channel)


def save_data(frame, geo_data, fluoro_data, frame_number):

    # create a json file with the geo and fluoro data
    timestamp = time.time()
    filename = os.path.join(OUTPUT_DIR, f"frame_{frame_number:04d}_{timestamp}.json")
    with open(filename, "w") as f:
        f.write(json.dumps({"frame_number": frame_number, "timestamp": timestamp, "geo_data": geo_data, "fluoro_data": fluoro_data}))
        
    save_image(image_data=frame.data, image_width=frame.width, image_height=frame.height, frame_number=frame_number, output_dir=OUTPUT_DIR, timestamp=timestamp)
    print(f"Saved: {filename}")
    
# continuuosly capture images until stop event is triggered
async def capture_images(duration=5):

    start_time = time.time()
    frame_number = 0

    async for frame in fluoro_service.get_frame_stream(plane=1):
        geo_data = await get_all_geometry()
        fluoro_data = await get_all_fluoro()

        print(geo_data)
        
        # Save the received data
        save_data(frame.frame.image, geo_data, fluoro_data, frame_number)
        
        frame_number += 1
        
        # Break the loop if the duration has elapsed
        if time.time() - start_time >= duration:
            break


# rotate the C-arm
async def rotate_c_arm():
    

    
    # get current C-arm position
    response = await geo_service.get_c_arm_pose()


async def get_all_geometry():

    params = {}
    table_position = await geo_service.get_table_position()
    params["table_position"] = table_position.to_json()
    # params["table_rotation"] = await geo_service.get_table_rotation()
    # params["carm_rotation"] = await geo_service.get_carm_rotation()
    # params["detector_rotation"] = await geo_service.get_detector_rotation()
    # params["zoom"] = await geo_service.get_zoom()
    # params["source_offset"] = await geo_service.get_source_offset()
    # params["detector_offset"] = await geo_service.get_detector_offset()
    # params["transforms"] = await geo_service.get_transforms()

    table_rotation = await geo_service.get_table_rotation()
    params["table_rotation"] = table_rotation.to_json()
    carm_rotation = await geo_service.get_carm_rotation()
    params["carm_rotation"] = carm_rotation.to_json()
    detector_rotation = await geo_service.get_detector_rotation()
    params["detector_rotation"] = detector_rotation.to_json()
    zoom = await geo_service.get_zoom()
    params["zoom"] = zoom.to_json()
    source_offset = await geo_service.get_source_offset()
    params["source_offset"] = source_offset.to_json()
    detector_offset = await geo_service.get_detector_offset()
    params["detector_offset"] = detector_offset.to_json()
    transforms = await geo_service.get_transforms()
    params["transforms"] = transforms.to_json()
    
    return params

async def get_all_fluoro():

    params = {}
    # params["xray_pedal"] = await fluoro_service.get_xray_pedal()
    # params["cine_pedal"] = await fluoro_service.get_cine_pedal()
    # params["shutters"] = await fluoro_service.get_shutters()
    # params["collimators"] = await fluoro_service.get_collimators()
    # params["frame_rate"] = await fluoro_service.get_frame_rate()
    # params["radiation"] = await fluoro_service.get_radiation()
    xray_pedal = await fluoro_service.get_xray_pedal()
    params["xray_pedal"] = xray_pedal.to_json()
    cine_pedal = await fluoro_service.get_cine_pedal()
    params["cine_pedal"] = cine_pedal.to_json()
    shutters = await fluoro_service.get_shutters()
    params["shutters"] = shutters.to_json()
    collimators = await fluoro_service.get_collimators()
    params["collimators"] = collimators.to_json()
    frame_rate = await fluoro_service.get_frame_rate()
    params["frame_rate"] = frame_rate.to_json()
    radiation = await fluoro_service.get_radiation()
    params["radiation"] = radiation.to_json()


    return params

async def main():


    response = await fluoro_service.get_xray_pedal()
    print(response.down)

    response = await fluoro_service.set_xray_pedal(down=True)
    print(response)

    await capture_images(5)

    response = await fluoro_service.get_xray_pedal()
    print(response)
    # don't forget to close the channel when done!
    channel.close()


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())

