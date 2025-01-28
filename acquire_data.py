import grpc
import time
import os
import cv2
import numpy as np
import math
import threading
from concurrent import futures

# Import the compiled gRPC classes
import fluoro_pb2
import fluoro_pb2_grpc
import common_pb2
import geometry_pb2
import geometry_pb2_grpc

# Set up gRPC connection
SIMULATOR_ADDRESS = "localhost:50051"  # Replace with actual simulator address
# Set up gRPC channel for fluoro
fluoro_channel = grpc.insecure_channel(SIMULATOR_ADDRESS)
fluoro_stub = fluoro_pb2_grpc.FluoroStub(fluoro_channel)

# Set up gRPC channel for geometry
# (You can reuse the same channel if the service runs on the same port,
#  or create a separate channel if needed. Here we reuse the same.)
geometry_stub = geometry_pb2_grpc.GeometryStub(fluoro_channel)

OUTPUT_DIR = "fluoroscopy_images"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def start_fluoroscopy():
    """Starts the fluoroscopy by setting the X-ray pedal to 'down'."""
    request = fluoro_pb2.SetXrayPedalRequest(down=True)
    response = fluoro_stub.SetXrayPedal(request)
    print(f"Fluoroscopy started: {response}")

def stop_fluoroscopy():
    """Stops the fluoroscopy by setting the X-ray pedal to 'up'."""
    request = fluoro_pb2.SetXrayPedalRequest(down=False)
    response = fluoro_stub.SetXrayPedal(request)
    print(f"Fluoroscopy stopped: {response}")

def save_image(image_data, image_width, image_height, frame_number):
    """Saves image data as a PNG file."""
    image_np = np.frombuffer(image_data, dtype=np.uint8)
    image_np = image_np.reshape((image_height, image_width))
    
    # convert to grayscale opencv image
    # image = cv2.cvtColor(image_np, cv2.COLOR_GRAY2BGR)
    image = image_np
    if image is not None:
        filename = os.path.join(OUTPUT_DIR, f"frame_{frame_number:04d}.png")
        cv2.imwrite(filename, image)
        print(f"Saved: {filename}")
    else:
        print("Error decoding image!")

def capture_images(duration=5):
    """
    Captures images from the fluoroscopy stream for a given duration.
    Saves the images to disk.
    """
    start_time = time.time()
    frame_number = 0

    request = fluoro_pb2.GetFrameStreamRequest(plane=fluoro_pb2.MAIN)
    responses = fluoro_stub.GetFrameStream(request)

    frame_buffer = []
    for response in responses:
        
        frame = response.frame
        image_width, image_height = frame.image.width, frame.image.height
        print(f"Frame {frame_number}: {image_width}x{image_height}")

        frame_buffer.append((frame, image_width, image_height, frame_number))
        # save_image(frame.image.data, image_width, image_height, frame_number)
        frame_number += 1

        if time.time() - start_time >= duration:
            break  # Stop capturing after the given duration
    print("Capturing done.")
    # Save all the frames in the buffer

    for frame, image_width, image_height, frame_number in frame_buffer:
        save_image(frame.image.data, image_width, image_height, frame_number)

    print("All frames saved.")


def read_frame_rate():
    """Reads the current frame rate of the fluoroscopy stream."""
    request = fluoro_pb2.GetFrameRateRequest()
    response = fluoro_stub.GetFrameRate(request)
    print(response)
    print(f"Frame rate: {response} fps")
    return response

def set_frame_rate(frame_rate):
    """Sets the frame rate of the fluoroscopy stream."""
    request = fluoro_pb2.SetFrameRateRequest(frame_rate=frame_rate)
    response = fluoro_stub.SetFrameRate(request)
    #print(f"Set frame rate: {response} fps")
    return response

def rotate_carm(angle_degrees, duration_seconds):
    """
    Slowly rotate the Carm from 0 to 90 degrees (pi/2 radians) in small increments.
    Adjust the axis (x, y, z) as needed for your simulator's coordinate system.
    """

    time_per_step = duration_seconds / angle_degrees
    # We'll rotate from 0 to 90 deg in 1-degree increments
    for deg in range(0, angle_degrees + 1):
        angle_radians = math.radians(deg)

        # Build the request.  Here we assume rotation about the Y-axis.
        # If you need a different axis, adjust x, y, z.
        request = geometry_pb2.SetCarmRotationRequest(
            plane=fluoro_pb2.MAIN,
            value=common_pb2.Vec3(x=0.0, y=angle_radians, z=0.0)
        )
        
        # Set Carm rotation
        response = geometry_stub.SetCarmRotation(request)
        print(f"Carm rotation set to {deg} degrees (y-axis).")

        # Sleep to slow down the rotation
        time.sleep(time_per_step)



def read_all_geometry():
    """
    Reads all geometry-related information using the available Get methods.
    
    Args:
        geometry_stub (GeometryStub): An instance of GeometryStub connected to the gRPC channel.
    
    Returns:
        dict: A dictionary containing geometry data.
    """
    geometry_data = {}
    
    try:
        geometry_data['TablePosition'] = geometry_stub.GetTablePosition(geometry_pb2.GetTablePositionRequest())
        geometry_data['TableRotation'] = geometry_stub.GetTableRotation(geometry_pb2.GetTableRotationRequest())
        geometry_data['CarmRotation'] = geometry_stub.GetCarmRotation(geometry_pb2.GetCarmRotationRequest())
        geometry_data['DetectorRotation'] = geometry_stub.GetDetectorRotation(geometry_pb2.GetDetectorRotationRequest())
        geometry_data['Zoom'] = geometry_stub.GetZoom(geometry_pb2.GetZoomRequest())
        geometry_data['SourceOffset'] = geometry_stub.GetSourceOffset(geometry_pb2.GetSourceOffsetRequest())
        geometry_data['DetectorOffset'] = geometry_stub.GetDetectorOffset(geometry_pb2.GetDetectorOffsetRequest())
        geometry_data['Transforms'] = geometry_stub.GetTransforms(geometry_pb2.GetTransformsRequest())
    except Exception as e:
        print(f"Error retrieving geometry data: {e}")
    
    return geometry_data
   

def read_all_fluoro():
    """
    Reads all fluoro-related information using the available Get methods.
    
    Args:
        fluoro_stub (Fluoro): An instance of Fluoro connected to the gRPC channel.
    
    Returns:
        dict: A dictionary containing fluoro data.
    """
    fluoro_data = {}
    
    try:
        fluoro_data['XrayPedal'] = fluoro_stub.GetXrayPedal(fluoro_pb2.GetXrayPedalRequest())
        fluoro_data['CinePedal'] = fluoro_stub.GetCinePedal(fluoro_pb2.GetCinePedalRequest())
        fluoro_data['Shutters'] = fluoro_stub.GetShutters(fluoro_pb2.GetShuttersRequest())
        fluoro_data['Collimators'] = fluoro_stub.GetCollimators(fluoro_pb2.GetCollimatorsRequest())
        fluoro_data['FrameRate'] = fluoro_stub.GetFrameRate(fluoro_pb2.GetFrameRateRequest())
        fluoro_data['Radiation'] = fluoro_stub.GetRadiation(fluoro_pb2.GetRadiationRequest())
    except Exception as e:
        print(f"Error retrieving fluoro data: {e}")
    
    return fluoro_data

# Run the process
if __name__ == "__main__":

    print("geometry data: ", read_all_geometry())
    print("fluoro data: ", read_all_fluoro())
    start_fluoroscopy()
    set_frame_rate(15)
    # capture_thread = threading.Thread(target=capture_images, daemon=True)
    # Create a thread that rotates the Carm
    # rotate_thread = threading.Thread(target=rotate_carm(90, 10), daemon=True)

    # Start capturing images
    # capture_thread.start()

    # Start rotating in the background
    #  rotate_thread.start()

    print("geometry data: ", read_all_geometry())
    print("fluoro data: ", read_all_fluoro())

    # Wait for the rotation to finish
    # rotate_thread.join()

    # Optionally, after rotation is done, you could stop fluoroscopy 
    # or continue capturing more images. For demonstration, we will
    # stop here. If you want indefinite capture, omit the stop_fluoroscopy().
    stop_fluoroscopy()

    # If you want to end the entire script, you can simply allow it to exit here.
    # If you needed to cleanly stop the capture_images() loop, you would need
    # additional logic (e.g., a shared flag) that breaks out of the for-loop inside
    # capture_images().
    #
    # If you want to wait for the capture thread to finish streaming:
    # capture_thread.join()
