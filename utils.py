import os
import cv2
import numpy as np

def save_image(image_data, image_width, image_height, frame_number, output_dir="fluoroscopy_images", timestamp=None):
    """Saves image data as a PNG file."""
    image_np = np.frombuffer(image_data, dtype=np.uint8)
    image_np = image_np.reshape((image_height, image_width))
    
    # convert to grayscale opencv image
    # image = cv2.cvtColor(image_np, cv2.COLOR_GRAY2BGR)
    image = image_np
    if image is not None:
        if timestamp:
            filename = os.path.join(output_dir, f"frame_{frame_number:04d}_{timestamp}.png")
        else:
            filename = os.path.join(output_dir, f"frame_{frame_number:04d}.png")
        cv2.imwrite(filename, image)
        print(f"Saved: {filename}")
    else:
        print("Error decoding image!")