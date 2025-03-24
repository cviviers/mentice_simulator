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
import mentice.mentice.mentice_philips.mentice_philips as mentice_philips
from utils import save_image
import json
from typing import AsyncGenerator, Optional
from typing import Any, List, Optional, Tuple
import time
import asyncio
from typing import Optional

OUTPUT_DIR = "fluoroscopy_images"
os.makedirs(OUTPUT_DIR, exist_ok=True)

from grpclib.client import Channel

channel = Channel(host="127.0.0.1", port=50051)
fluoro_service = fluoro.FluoroStub(channel)
fluoro_service_lock = asyncio.Lock()
geo_service = geometry.GeometryStub(channel)
geo_service_lock = asyncio.Lock()
mentice_philips_service = mentice_philips.APIStub(channel)
mentice_philips_service_lock = asyncio.Lock()


def save_data(frame, geo_data, fluoro_data, frame_number, orientation=None):

    # create a json file with the geo and fluoro data
    timestamp = time.time()
    filename = os.path.join(OUTPUT_DIR, "json", f"frame_{frame_number:04d}_{timestamp}.json")
    with open(filename, "w") as f:
        f.write(json.dumps({"frame_number": frame_number, "timestamp": timestamp, "geo_data": geo_data, "fluoro_data": fluoro_data, "orientation": orientation.to_json()}))
        
    save_image(image_data=frame.data, image_width=frame.width, image_height=frame.height, frame_number=frame_number, output_dir=OUTPUT_DIR, timestamp=timestamp, orientation=orientation)
    print(f"Saved: {filename}")


async def capture_images(duration=5, interval=0.05, single_frame=True, orientation=common.Vec3()) -> None:
    """
    Continuously capture images from `fluoro_service` for `duration` seconds,
    polling every `interval` seconds, and fetch geometry data from `geo_service`.

    Args:
        duration:       How many seconds to keep capturing frames.
        interval:       How many seconds between checks for the next frame.

    Returns:
        A list of captured data (frame image, geometry data, frame_number).
    """
    start_time = time.time()
    frame_number = 0
    buffer = []

    # We’ll read frames from this asynchronous generator
    frame_generator = fluoro_service.get_frame_stream(plane=1)

    # This variable will hold the *latest* frame fetched in the background
    frame: Optional[object] = None

    async def fetch_next_frame():
        """
        Fetch the next frame if available. If the stream ends, sets `frame` to None.
        """
        nonlocal frame
        try:
            frame = await anext(frame_generator)
        except StopAsyncIteration:
            frame = None

    # Start fetching the first frame in the background
    # (We store the task in a variable in case we want to check its status later.)
    frame_task = asyncio.create_task(fetch_next_frame())

    # Keep going until `duration` expires
    while True:
        # If there's a newly fetched frame, process it
        if frame is not None:
            # Move the current frame to a local variable
            current_frame = frame

            # Immediately schedule the next fetch
            frame_task = asyncio.create_task(fetch_next_frame())

            # --- Now call your geometry service (async) ---
            # async with geo_service_lock:
            geo_data = await get_minimal_geometry()
            # geo_data = None #await get_all_geometry_if_free()
            if geo_data is None:
                print("Skipping frame due to geo_service being busy.")
                continue
            # You could also fetch other data here, e.g.:
            fluoro_data = None #await get_all_fluoro_concurrent()

            print(f"Received frame #{frame_number}")
            print(f"Geo data: {geo_data}")
            # print(f"Fluoro data: {fluoro_data}")

            # Append the combined data to buffer
            buffer.append(
                (
                    current_frame.frame.image,
                    geo_data,
                    fluoro_data,
                    frame_number,
                    orientation
                    
                )
            
            )
        
            frame_number += 1
        else:
            # If we're out of frames, break the loop
            print("No more frames available.")
            if time.time() - start_time >= duration:
                break
        # Sleep for 'interval' seconds so we don't hammer CPU or spam requests
        await asyncio.sleep(interval)

        # Stop if we've reached the desired duration
        if time.time() - start_time >= duration:
            break

    if single_frame:
        # only keep last image
        buffer = buffer[-1:]
    print("Saving data...")
    for data in buffer:
        save_data(*data)

async def capture_single_image(wait_time: float = 0.1, orientation=common.Vec3()) -> None:
    """
    Wait a brief moment and then capture a single image along with its associated geometry 
    and fluoro data by opening and closing the fluoro stream once.

    Args:
        wait_time: Time (in seconds) to wait before capturing the image.
        orientation: Orientation data to associate with the capture.
    """
    # Wait a few fractions of a second before capturing.
    await asyncio.sleep(wait_time)

    # Capture a single frame from the fluoro stream.
    try:
        frame_generator = fluoro_service.get_frame_stream(plane=1)
        frame = await frame_generator.__anext__()
    except StopAsyncIteration:
        print("No frame available from the fluoro stream.")
        return
    except Exception as e:
        print(f"Error opening fluoro stream: {e}")
        return

    # Fetch the minimal geometry data.
    try:
        geo_data = await get_minimal_geometry()
    except Exception as e:
        print(f"Error fetching geometry data: {e}")
        return

    if geo_data is None:
        print("Skipping frame due to unavailable geometry data.")
        return

    # Optionally, fetch additional fluoro data.
    try:
        fluoro_data = await get_all_fluoro()
    except Exception as e:
        print(f"Error fetching additional fluoro data: {e}")
        fluoro_data = None

    # Log that we captured the image.
    print("Captured single image")
    captured_data = (
        frame.frame.image,  # Adjust if your frame object structure differs.
        geo_data,
        fluoro_data,
        0,  # Frame number (only one image captured).
        orientation
    )

    # Save the captured data.
    print("Saving data...")
    save_data(*captured_data)


async def capture_images_data_one_at_a_time(duration: float = 5, interval: float = 0.05, single_image = True, orientation=common.Vec3()) -> None:
    """
    For a specified duration, capture a single image and its associated geometry and 
    fluoro data by opening and closing the fluoro stream for each capture cycle.

    Args:
        duration: Total time (in seconds) over which to capture data.
        interval: Delay (in seconds) between successive captures.
    """
    start_time = time.monotonic()
    frame_number = 0
    captured_data: List[Tuple[Any, Any, Optional[Any], int]] = []

    while time.monotonic() - start_time < duration:
        # Open the fluoro stream, capture a single frame, and close the stream.
        try:
            # Assuming that the fluoro service stream supports async context management.
            frame_generator = fluoro_service.get_frame_stream(plane=1)
            try:
                frame = await frame_generator.__anext__()
            except StopAsyncIteration:
                print("No frame available from the fluoro stream.")
                await asyncio.sleep(interval)
                continue
        except Exception as e:
            print(f"Error opening fluoro stream: {e}")
            await asyncio.sleep(interval)
            continue

        # Fetch geometry data.
        try:
            geo_data = await get_minimal_geometry()
        except Exception as e:
            print(f"Error fetching geometry data: {e}")
            geo_data = None

        if geo_data is None:
            print("Skipping frame due to unavailable geometry data.")
            await asyncio.sleep(interval)
            continue

        # Optionally, fetch additional fluoro data.
        try:
            # Uncomment the following line if get_all_fluoro_concurrent() is available.
            fluoro_data = await get_all_fluoro()
            # fluoro_data = None
        except Exception as e:
            print(f"Error fetching additional fluoro data: {e}")
            fluoro_data = None

        # Log the captured data.
        print(f"Captured frame #{frame_number}")
        print(f"Geometry data: {geo_data}")
        # If you wish to log fluoro_data as well, uncomment the next line:
        # print(f"Fluoro data: {fluoro_data}")

        # Buffer the captured data.
        captured_data.append((
            frame.frame.image,  # Adjust if your frame object has a different structure.
            geo_data,
            fluoro_data,
            frame_number,
            orientation
        ))
        frame_number += 1

        # Wait before the next capture cycle.
        await asyncio.sleep(interval)

    # After capturing, save all the buffered data.
    if single_image:
        # only keep last image
        captured_data = captured_data[-1:]
    print("Saving data...")
    for data in captured_data:
        save_data(*data)

def convert_to_degrees(vector = common.Vec3(x=-40.0, y=-40.0, z=-40.0)):
    return common.Vec3(x=math.degrees(vector.x), y=math.degrees(vector.y), z=math.degrees(vector.z))

def convert_to_radians(vector = common.Vec3(x=-40.0, y=-40.0, z=-40.0)):
    return common.Vec3(x=math.radians(vector.x), y=math.radians(vector.y), z=math.radians(vector.z))


async def rotate_carm(
    start_angle_degrees: common.Vec3 = common.Vec3(x=-40.0, y=-40.0, z=-40.0),
    target_angle_degrees: common.Vec3 = common.Vec3(x=40.0, y=40.0, z=40.0),
    duration_seconds: float = 5.0,
    increments: int = 20
):
  
    
    print(f"Starting angle: {start_angle_degrees}")
    print(f"Target angle: {target_angle_degrees}°")

    # move to the starting position
    print("Moving to starting position...")
    await geo_service.set_carm_rotation(
        plane=fluoro.Plane.MAIN,
        value=convert_to_radians(start_angle_degrees)
    )
    print("C-arm rotation set to starting position.")

    # angle_diff = target_angle_degrees - start_angle_degrees
    time_per_step = duration_seconds

    # 1. prop x rotate
    # 2. carm y rotate
    # 3. z rotate

    # for prop in range(start_angle_degrees.x, target_angle_degrees.x, increments):
    #     for carm in range(start_angle_degrees.y, target_angle_degrees.y, increments):
    #         for zarm in range(start_angle_degrees.z, target_angle_degrees.z, increments):

    # for roll in range(0, 10, increments):
    for roll in range(int(start_angle_degrees.x), int(target_angle_degrees.x), increments):
        # for prop in range(0, 10, increments):
        for prop in range(int(start_angle_degrees.y), int(target_angle_degrees.y), increments):
            # for zarm in range(0, 10, increments):
            for zarm in range(int(start_angle_degrees.z), int(target_angle_degrees.z), increments):


                print(f"Setting C-arm rotation to: {roll:.2f}°, {prop:.2f}°, {zarm:.2f}°")
                await geo_service.set_carm_rotation(
                    plane=fluoro.Plane.MAIN,
                    value=convert_to_radians(common.Vec3(x=roll, y=prop, z=zarm))
                )


                await capture_images(duration=1, interval=0.05, single_frame=True, orientation=common.Vec3(x=roll, y=prop, z=zarm))
                # await capture_single_image(orientation=common.Vec3(x=roll, y=prop, z=zarm))

                await asyncio.sleep(time_per_step)

    # for step in range(steps + 1):
    #     # Fraction of the way we are through the rotation
    #     fraction = step / steps
        
    #     # Current angle, linearly interpolated
    #     current_angle_degrees = start_angle_degrees + angle_diff * fraction
    #     current_angle_radians = math.radians(current_angle_degrees)
        
    #     # Rotate the C-arm
        
    #     await geo_service.set_carm_rotation(
    #         plane=fluoro.Plane.MAIN,
    #         value=common.Vec3(x=0.0, y=current_angle_radians, z=0.0)
    #     )
        
    #     # Log the update
    #     print(
    #         f"Step {step}/{steps}:"
    #         f" C-arm rotation set to {current_angle_degrees:.2f}° "
    #         f"(= {current_angle_radians:.2f} rad)."
    #     )

    #     await capture_images_data_one_at_a_time(2)
        
    #     # Sleep between steps (skip final sleep if we're done)
    #     if step < steps:
    #         await asyncio.sleep(time_per_step)

    print("C-arm rotation complete!")


async def get_all_geometry():

    params = {}
    # check if geo_service is busy, if not, make the call

    table_position = await geo_service.get_table_position()
    params["table_position"] = table_position.to_json()
    # table_rotation = await geo_service.get_table_rotation()
    # params["table_rotation"] = table_rotation
    # carm_rotation = await geo_service.get_carm_rotation()
    # params["carm_rotation"] = carm_rotation
    # detector_rotation = await geo_service.get_detector_rotation()
    # params["detector_rotation"] = detector_rotation
    # zoom = await geo_service.get_zoom()
    # params["zoom"] = zoom
    # source_offset = await geo_service.get_source_offset()
    # params["source_offset"] = source_offset
    # detector_offset = await geo_service.get_detector_offset()
    # params["detector_offset"] = detector_offset
    # transforms = await geo_service.get_transforms()
    # params["transforms"] = transforms
    return params

async def get_all_geometry_if_free():
    """
    Attempt to fetch geometry data only if geo_service is not busy.
    Returns None if busy; otherwise returns the geometry.
    """
    # If the lock is currently held, it means geo_service is "busy."
    if geo_service_lock.locked():
        print("geo_service is busy, skipping geometry call.")
        return None
    
    # Otherwise, we'll acquire the lock and do the call
    async with geo_service_lock:
        return await get_all_geometry()

async def get_minimal_geometry():

    params = {}
    table_position = await geo_service.get_table_position()
    params["table_position"] = table_position.to_json()
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

async def get_system_state():
    
    sys_state = await mentice_philips_service.get_system_state(plane=fluoro.Plane.MAIN)
    return sys_state

async def get_all_fluoro():

    params = {}
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

async def get_all_fluoro_concurrent():
    try:
        results = await asyncio.gather(
            fluoro_service.get_xray_pedal(),
            fluoro_service.get_cine_pedal(),
            fluoro_service.get_shutters(),
            fluoro_service.get_collimators(),
            fluoro_service.get_frame_rate(),
            fluoro_service.get_radiation(),
            return_exceptions=True  # Prevents entire function from failing if one call fails
        )

        param_names = ["xray_pedal", "cine_pedal", "shutters", "collimators", "frame_rate", "radiation"]
        params = {}

        for name, result in zip(param_names, results):
            if isinstance(result, Exception):
                print(f"Error retrieving {name}: {result}")
                params[name] = None
            else:
                params[name] = result.to_json()

        return params
    except Exception as e:
        print(f"Unexpected error in get_all_fluoro(): {e}")
        return None

async def reset_geometry():
    await geo_service.set_carm_rotation(plane=fluoro.Plane.MAIN, value=common.Vec3(x=0.0, y=0.0, z=0.0))
    print(f"Carm rotation reset.")
    # move table lower
    
    # await geo_service.set_table_position(value=common.Vec3(x=2.5, y=-28.0, z=0.0))
    await geo_service.set_table_position(value=common.Vec3(x=0, y=0, z=0.0))
    print(f"Table reset.")
    table_position = await geo_service.get_table_position()
    print(table_position.value)
    # set the source detector distance
    # await geo_service.set_detector_offset(plane=fluoro.Plane.MAIN, offset=0.38499998827222948*1)
    print(f"Detector offset reset.")
    await geo_service.set_source_offset(offset= (1.1950000000000003 - 0.38499998827222948)*100)
    print(f"Source offset reset.")
    #await geo_service.set_zoom(plane=fluoro.Plane.MAIN, value=0.0)
    print(f"Zoom reset.")
    # print all geo 
    geo_data = await get_minimal_geometry()
    print(geo_data)
async def set_starting_geometry(table_position, carm_rotation):
    
    response = await geo_service.set_carm_rotation(plane=fluoro.Plane.MAIN, value=carm_rotation)
    print(f"Carm rotation set.")
    print(response)
    c_arm_rotation = await geo_service.get_carm_rotation()
    print(c_arm_rotation.value)
    response = await geo_service.set_table_position(value=table_position)
    print(f"Table position set.")
    print(response)
    table_position = await geo_service.get_table_position()
    print(table_position.value)

async def set_fluoro_params():

    await fluoro_service.set_frame_rate(frame_rate=15)
    # print(response.)
  

async def main():

    # reset the geometry
    await reset_geometry()
    # wait a bit for the geometry to reset

    await set_starting_geometry(table_position=common.Vec3(x=2.5, y=-29.0, z=0.0), carm_rotation=common.Vec3(x=0.0, y=0.0, z=0.0))
    await set_fluoro_params()

    response = await fluoro_service.get_xray_pedal()
    if response.down==False:
        response = await fluoro_service.set_xray_pedal(down=True)
        

    
    # Run `capture_images()` and `rotate_carm()` in parallel
    await rotate_carm(start_angle_degrees=common.Vec3(x=-40.0, y=-40.0, z=-40.0), target_angle_degrees=common.Vec3(x=50.0, y=50.0, z=50.0), duration_seconds=2.0, increments=10)

    # await c_arm_rotate_task
    response = await fluoro_service.set_xray_pedal(down=False)
  

    response = await fluoro_service.get_xray_pedal()
    if response.down==True:
        response = await fluoro_service.set_xray_pedal(down=False)
 

    # await reset_geometry()
    # don't forget to close the channel when done!
    
    channel.close()
    

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
