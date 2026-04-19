from plugins.base_plugin.base_plugin import BasePlugin
from PIL import Image, ImageOps, ImageColor
import logging
import os
import random
import io
import base64
import shutil

from utils.image_utils import pad_image_blur

logger = logging.getLogger(__name__)
IMAGE_FOLDER_ENV = "INKYPI_IMAGE_FOLDER"

def list_files_in_folder(folder_path):
    """Return a list of image file paths in the given folder, excluding hidden files."""
    image_extensions = ('.avif', '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.heif', '.heic')
    image_files = []
    for root, dirs, files in os.walk(folder_path):
        for f in files:
            if f.lower().endswith(image_extensions) and not f.startswith('.'):
                image_files.append(os.path.join(root, f))

    return image_files

class ImageFolder(BasePlugin):
    def generate_settings_template(self):
        """Override to include the image folder path from environment variable."""
        template_params = super().generate_settings_template()
        
        # Get the image folder path from environment variable
        folder_path = os.getenv(IMAGE_FOLDER_ENV)
        if folder_path:
            folder_path = os.path.expanduser(folder_path)
        
        template_params['image_folder_path'] = folder_path
        return template_params
    
    def generate_image(self, settings, device_config):
        logger.info("=== Image Folder Plugin: Starting image generation ===")

        folder_path = os.getenv(IMAGE_FOLDER_ENV)
        if not folder_path:
            logger.error(f"Environment variable {IMAGE_FOLDER_ENV} is not set")
            raise RuntimeError(f"Image folder environment variable {IMAGE_FOLDER_ENV} is not configured.")

        folder_path = os.path.expanduser(folder_path)

        if not os.path.exists(folder_path):
            logger.error(f"Folder does not exist: {folder_path}")
            raise RuntimeError(f"Folder does not exist: {folder_path}")

        if not os.path.isdir(folder_path):
            logger.error(f"Path is not a directory: {folder_path}")
            raise RuntimeError(f"Path is not a directory: {folder_path}")

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]
            logger.debug(f"Vertical orientation detected, dimensions: {dimensions[0]}x{dimensions[1]}")

        logger.info(f"Scanning folder: {folder_path}")
        image_files = list_files_in_folder(folder_path)

        if not image_files:
            logger.warning(f"No image files found in folder: {folder_path}")
            raise RuntimeError(f"No image files found in folder: {folder_path}")

        logger.debug(f"Found {len(image_files)} image file(s) in folder")
        image_url = random.choice(image_files)
        logger.info(f"Selected random image: {os.path.basename(image_url)}")
        logger.debug(f"Full path: {image_url}")

        # Check padding options
        use_padding = settings.get('padImage') == "true"
        background_option = settings.get('backgroundOption', 'blur')
        logger.debug(f"Settings: pad_image={use_padding}, background_option={background_option}")

        try:
            # Use adaptive loader for memory-efficient processing
            # Load without auto-resize first to handle padding options
            # Note: Loader automatically handles EXIF orientation correction
            img = self.image_loader.from_file(image_url, dimensions, resize=False)

            if not img:
                raise RuntimeError("Failed to load image from file")

            if use_padding:
                logger.debug(f"Applying padding with {background_option} background")
                if background_option == "blur":
                    img = pad_image_blur(img, dimensions)
                else:
                    background_color = ImageColor.getcolor(settings.get('backgroundColor') or "white", img.mode)
                    img = ImageOps.pad(img, dimensions, color=background_color, method=Image.Resampling.LANCZOS)
            else:
                # No padding requested, scale to fit dimensions (crop to preserve aspect ratio)
                logger.debug(f"Scaling to fit dimensions: {dimensions[0]}x{dimensions[1]}")
                img = ImageOps.fit(img, dimensions, method=Image.LANCZOS)

            return img
        except Exception as e:
            logger.error(f"Error loading image from {image_url}: {e}")
            raise RuntimeError("Failed to load image, please check logs.")

        logger.info("=== Image Folder Plugin: Image generation complete ===")
        return img

    def get_folder_path(self):
        """Get the folder path from environment variable."""
        folder_path = os.getenv(IMAGE_FOLDER_ENV)
        if not folder_path:
            raise RuntimeError(f"Environment variable {IMAGE_FOLDER_ENV} is not set")
        return os.path.expanduser(folder_path)

    def get_all_images(self):
        """Get list of all image files in the folder with metadata."""
        try:
            folder_path = self.get_folder_path()
            
            if not os.path.exists(folder_path):
                raise RuntimeError(f"Folder does not exist: {folder_path}")
            
            if not os.path.isdir(folder_path):
                raise RuntimeError(f"Path is not a directory: {folder_path}")
            
            images = []
            image_files = list_files_in_folder(folder_path)
            
            for image_path in sorted(image_files):
                try:
                    size_bytes = os.path.getsize(image_path)
                    filename = os.path.basename(image_path)
                    relative_path = os.path.relpath(image_path, folder_path)
                    
                    images.append({
                        'filename': filename,
                        'relative_path': relative_path,
                        'full_path': image_path,
                        'size_bytes': size_bytes,
                        'size_kb': round(size_bytes / 1024, 2)
                    })
                except Exception as e:
                    logger.warning(f"Error processing image {image_path}: {e}")
            
            return images
        except Exception as e:
            logger.error(f"Error getting all images: {e}")
            raise

    def get_image_thumbnail(self, relative_path, thumbnail_size=(200, 200)):
        """Get base64-encoded thumbnail for an image."""
        try:
            folder_path = self.get_folder_path()
            full_path = os.path.abspath(os.path.join(folder_path, relative_path))
            
            # Security check: ensure the path is within the folder
            if not full_path.startswith(os.path.abspath(folder_path)):
                raise RuntimeError("Invalid path: attempting to access file outside folder")
            
            if not os.path.isfile(full_path):
                raise RuntimeError(f"File not found: {full_path}")
            
            # Load and resize image
            img = Image.open(full_path)
            img.thumbnail(thumbnail_size, Image.Resampling.LANCZOS)
            
            # Convert to base64
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=85)
            buffer.seek(0)
            img_base64 = base64.b64encode(buffer.getvalue()).decode()
            
            return f"data:image/jpeg;base64,{img_base64}"
        except Exception as e:
            logger.error(f"Error generating thumbnail for {relative_path}: {e}")
            raise

    def delete_image(self, relative_path):
        """Delete an image file from the folder."""
        try:
            folder_path = self.get_folder_path()
            full_path = os.path.abspath(os.path.join(folder_path, relative_path))
            
            # Security check: ensure the path is within the folder
            if not full_path.startswith(os.path.abspath(folder_path)):
                raise RuntimeError("Invalid path: attempting to delete file outside folder")
            
            if not os.path.isfile(full_path):
                raise RuntimeError(f"File not found: {full_path}")
            
            os.remove(full_path)
            logger.info(f"Deleted image: {full_path}")
            return True
        except Exception as e:
            logger.error(f"Error deleting image {relative_path}: {e}")
            raise

    def upload_image(self, file_obj, allowed_extensions=None):
        """Upload a new image file to the folder.
        
        Args:
            file_obj: File object from Flask request.files
            allowed_extensions: Tuple of allowed extensions (e.g., ('jpg', 'jpeg', 'png'))
        
        Returns:
            dict with uploaded file info
        """
        try:
            if allowed_extensions is None:
                allowed_extensions = ('avif', 'jpg', 'jpeg', 'png', 'gif', 'bmp', 'tiff', 'webp', 'heif', 'heic')
            
            folder_path = self.get_folder_path()
            
            if not file_obj or file_obj.filename == '':
                raise RuntimeError("No file selected")
            
            # Check file extension
            filename = file_obj.filename
            if '.' not in filename:
                raise RuntimeError("File must have an extension")
            
            ext = filename.rsplit('.', 1)[1].lower()
            if ext not in allowed_extensions:
                raise RuntimeError(f"File type not allowed. Allowed types: {', '.join(allowed_extensions)}")
            
            # Ensure unique filename if it already exists
            full_path = os.path.join(folder_path, filename)
            if os.path.exists(full_path):
                name, ext = os.path.splitext(filename)
                counter = 1
                while os.path.exists(full_path):
                    filename = f"{name}_{counter}{ext}"
                    full_path = os.path.join(folder_path, filename)
                    counter += 1
            
            # Validate it's actually an image
            try:
                img = Image.open(file_obj.stream)
                img.verify()
            except Exception as e:
                raise RuntimeError(f"Invalid image file: {e}")
            
            # Save the file
            file_obj.save(full_path)
            logger.info(f"Uploaded image: {full_path}")
            
            size_bytes = os.path.getsize(full_path)
            return {
                'filename': filename,
                'relative_path': os.path.relpath(full_path, folder_path),
                'size_bytes': size_bytes,
                'size_kb': round(size_bytes / 1024, 2)
            }
        except Exception as e:
            logger.error(f"Error uploading image: {e}")
            raise

