from flask import Blueprint, request, jsonify, current_app, render_template, send_from_directory, send_file
from plugins.plugin_registry import get_plugin_instance
from utils.app_utils import resolve_path, handle_request_files, parse_form
from refresh_task import ManualRefresh, PlaylistRefresh
import json
import os
import logging

logger = logging.getLogger(__name__)
plugin_bp = Blueprint("plugin", __name__)

def _delete_plugin_instance_images(device_config, plugin_instance_obj):
    """Delete all images associated with a plugin instance."""
    # Delete the plugin instance's generated image
    plugin_image_path = os.path.join(device_config.plugin_image_dir, plugin_instance_obj.get_image_path())
    if os.path.exists(plugin_image_path):
        try:
            os.remove(plugin_image_path)
            logger.info(f"Deleted plugin instance image: {plugin_image_path}")
        except Exception as e:
            logger.warning(f"Failed to delete plugin instance image {plugin_image_path}: {e}")

    # Call the plugin's cleanup method to handle plugin-specific resource cleanup
    try:
        plugin_config = device_config.get_plugin(plugin_instance_obj.plugin_id)
        if plugin_config:
            plugin = get_plugin_instance(plugin_config)
            plugin.cleanup(plugin_instance_obj.settings)
    except Exception as e:
        logger.warning(f"Error during plugin cleanup for {plugin_instance_obj.plugin_id}: {e}")

# Removed module-level PLUGINS_DIR - will resolve dynamically in route handlers

@plugin_bp.route('/plugin/<plugin_id>')
def plugin_page(plugin_id):
    device_config = current_app.config['DEVICE_CONFIG']
    playlist_manager = device_config.get_playlist_manager()

    # Find the plugin by id
    plugin_config = device_config.get_plugin(plugin_id)
    if plugin_config:
        try:
            plugin = get_plugin_instance(plugin_config)
            template_params = plugin.generate_settings_template()

            # retrieve plugin instance from the query parameters if updating existing plugin instance
            plugin_instance_name = request.args.get('instance')
            if plugin_instance_name:
                plugin_instance = playlist_manager.find_plugin(plugin_id, plugin_instance_name)
                if not plugin_instance:
                    return jsonify({"error": f"Plugin instance: {plugin_instance_name} does not exist"}), 500

                # add plugin instance settings to the template to prepopulate
                template_params["plugin_settings"] = plugin_instance.settings
                template_params["plugin_instance"] = plugin_instance_name
                template_params["refresh_settings"] = plugin_instance.refresh

            template_params["playlists"] = playlist_manager.get_playlist_names()
        except Exception as e:
            logger.exception("EXCEPTION CAUGHT: " + str(e))
            return jsonify({"error": f"An error occurred: {str(e)}"}), 500
        return render_template('plugin.html', plugin=plugin_config, **template_params)
    else:
        return "Plugin not found", 404

@plugin_bp.route('/images/<plugin_id>/<path:filename>')
def image(plugin_id, filename):
    # Resolve plugins directory dynamically
    plugins_dir = resolve_path("plugins")

    # Construct the full path to the plugin's file
    plugin_dir = os.path.join(plugins_dir, plugin_id)

    # Security check to prevent directory traversal
    safe_path = os.path.abspath(os.path.join(plugin_dir, filename))
    if not safe_path.startswith(os.path.abspath(plugins_dir)):
        return "Invalid path", 403

    # Convert to absolute path for send_from_directory
    abs_plugin_dir = os.path.abspath(plugin_dir)

    # Check if the directory and file exist
    if not os.path.isdir(abs_plugin_dir):
        logger.error(f"Plugin directory not found: {abs_plugin_dir}")
        return "Plugin directory not found", 404

    if not os.path.isfile(safe_path):
        logger.error(f"File not found: {safe_path}")
        return "File not found", 404

    # Serve the file from the plugin directory
    return send_from_directory(abs_plugin_dir, filename)

@plugin_bp.route('/plugin_instance_image/<path:playlist_name>/<path:plugin_id>/<path:instance_name>')
def plugin_instance_image(playlist_name, plugin_id, instance_name):
    """Serve the generated image for a plugin instance."""
    device_config = current_app.config['DEVICE_CONFIG']
    playlist_manager = device_config.get_playlist_manager()

    # Find the plugin instance
    playlist = playlist_manager.get_playlist(playlist_name)
    if not playlist:
        return "Playlist not found", 404

    plugin_instance = playlist.find_plugin(plugin_id, instance_name)
    if not plugin_instance:
        return "Plugin instance not found", 404

    # Get the image path
    image_filename = plugin_instance.get_image_path()
    image_path = os.path.join(device_config.plugin_image_dir, image_filename)

    # Check if the image exists
    if not os.path.exists(image_path):
        # Return a placeholder or 404
        return "Image not yet generated", 404

    # Serve the image
    return send_from_directory(device_config.plugin_image_dir, image_filename)

@plugin_bp.route('/delete_plugin_instance', methods=['POST'])
def delete_plugin_instance():
    device_config = current_app.config['DEVICE_CONFIG']
    playlist_manager = device_config.get_playlist_manager()

    data = request.json
    playlist_name = data.get("playlist_name")
    plugin_id = data.get("plugin_id")
    plugin_instance = data.get("plugin_instance")

    try:
        playlist = playlist_manager.get_playlist(playlist_name)
        if not playlist:
            return jsonify({"success": False, "message": "Playlist not found"}), 400

        # Get the plugin instance to find associated images
        plugin_instance_obj = playlist.find_plugin(plugin_id, plugin_instance)
        if not plugin_instance_obj:
            return jsonify({"success": False, "message": "Plugin instance not found"}), 400

        # Delete associated images before removing from playlist
        _delete_plugin_instance_images(device_config, plugin_instance_obj)

        result = playlist.delete_plugin(plugin_id, plugin_instance)
        if not result:
            return jsonify({"success": False, "message": "Plugin instance not found"}), 400

        # save changes to device config file
        device_config.write_config()

    except Exception as e:
        logger.exception("EXCEPTION CAUGHT: " + str(e))
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

    return jsonify({"success": True, "message": "Deleted plugin instance."})

@plugin_bp.route('/update_plugin_instance/<string:instance_name>', methods=['PUT'])
def update_plugin_instance(instance_name):
    device_config = current_app.config['DEVICE_CONFIG']
    playlist_manager = device_config.get_playlist_manager()

    try:
        form_data = parse_form(request.form)

        if not instance_name:
            raise RuntimeError("Instance name is required")

        plugin_id = form_data.pop("plugin_id")
        plugin_instance = playlist_manager.find_plugin(plugin_id, instance_name)
        if not plugin_instance:
            return jsonify({"error": f"Plugin instance: {instance_name} does not exist"}), 500

        # Handle refresh settings if provided
        refresh_settings_json = form_data.pop("refresh_settings", None)
        if refresh_settings_json:
            from utils.time_utils import calculate_seconds
            refresh_settings = json.loads(refresh_settings_json)
            refresh_type = refresh_settings.get('refreshType')

            if refresh_type == "interval":
                unit = refresh_settings.get('unit')
                interval = refresh_settings.get('interval')
                if unit and interval:
                    refresh_interval_seconds = calculate_seconds(int(interval), unit)
                    plugin_instance.refresh = {"interval": refresh_interval_seconds}
            elif refresh_type == "scheduled":
                refresh_time = refresh_settings.get('refreshTime')
                if refresh_time:
                    plugin_instance.refresh = {"scheduled": refresh_time}

        # Only update plugin settings if there's actual data (not just refresh settings)
        plugin_settings = form_data
        plugin_settings.update(handle_request_files(request.files, request.form))

        if plugin_settings:  # Only update if there are actual plugin settings
            plugin_instance.settings = plugin_settings

        device_config.write_config()
    except Exception as e:
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500
    return jsonify({"success": True, "message": f"Updated plugin instance {instance_name}."})

@plugin_bp.route('/display_plugin_instance', methods=['POST'])
def display_plugin_instance():
    device_config = current_app.config['DEVICE_CONFIG']
    refresh_task = current_app.config['REFRESH_TASK']
    playlist_manager = device_config.get_playlist_manager()

    data = request.json
    playlist_name = data.get("playlist_name")
    plugin_id = data.get("plugin_id")
    plugin_instance_name = data.get("plugin_instance")

    try:
        playlist = playlist_manager.get_playlist(playlist_name)
        if not playlist:
            return jsonify({"success": False, "message": f"Playlist {playlist_name} not found"}), 400

        plugin_instance = playlist.find_plugin(plugin_id, plugin_instance_name)
        if not plugin_instance:
            return jsonify({"success": False, "message": f"Plugin instance '{plugin_instance_name}' not found"}), 400

        refresh_task.manual_update(PlaylistRefresh(playlist, plugin_instance, force=True))
    except Exception as e:
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

    return jsonify({"success": True, "message": "Display updated"}), 200

@plugin_bp.route('/update_now', methods=['POST'])
def update_now():
    device_config = current_app.config['DEVICE_CONFIG']
    refresh_task = current_app.config['REFRESH_TASK']
    display_manager = current_app.config['DISPLAY_MANAGER']

    try:
        plugin_settings = parse_form(request.form)
        plugin_settings.update(handle_request_files(request.files))
        plugin_id = plugin_settings.pop("plugin_id")

        # Check if refresh task is running
        if refresh_task.running:
            refresh_task.manual_update(ManualRefresh(plugin_id, plugin_settings))
        else:
            # In development mode, directly update the display
            logger.info("Refresh task not running, updating display directly")
            plugin_config = device_config.get_plugin(plugin_id)
            if not plugin_config:
                return jsonify({"error": f"Plugin '{plugin_id}' not found"}), 404

            plugin = get_plugin_instance(plugin_config)
            image = plugin.generate_image(plugin_settings, device_config)
            display_manager.display_image(image, image_settings=plugin_config.get("image_settings", []))

        return jsonify({"success": True, "message": "Display updated"}), 200
    except Exception as e:
        logger.exception(f"Error in update_now: {str(e)}")
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

# ============ Image Folder Plugin Endpoints ============

@plugin_bp.route('/image_folder/images', methods=['GET'])
def image_folder_list_images():
    """Get list of all images in the image_folder."""
    try:
        import os
        from plugins.image_folder.image_folder import list_files_in_folder, IMAGE_FOLDER_ENV
        
        folder_path = os.getenv(IMAGE_FOLDER_ENV)
        if not folder_path:
            return jsonify({"success": False, "error": f"Environment variable {IMAGE_FOLDER_ENV} not set"}), 400
        
        folder_path = os.path.expanduser(folder_path)
        
        if not os.path.exists(folder_path):
            return jsonify({"success": False, "error": f"Folder does not exist: {folder_path}"}), 400
        
        if not os.path.isdir(folder_path):
            return jsonify({"success": False, "error": f"Path is not a directory: {folder_path}"}), 400
        
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
                    'size_bytes': size_bytes,
                    'size_kb': round(size_bytes / 1024, 2)
                })
            except Exception as e:
                logger.warning(f"Error processing image {image_path}: {e}")
        
        return jsonify({
            "success": True,
            "images": images,
            "count": len(images)
        }), 200
    except Exception as e:
        logger.error(f"Error listing images: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@plugin_bp.route('/image_folder/thumbnail', methods=['GET'])
def image_folder_get_thumbnail():
    """Get thumbnail image for a file."""
    try:
        import os
        import io
        from PIL import Image
        from plugins.image_folder.image_folder import IMAGE_FOLDER_ENV
        
        relative_path = request.args.get('path')
        if not relative_path:
            # Return a placeholder image for missing path
            placeholder = Image.new('RGB', (200, 200), color=(220, 220, 220))
            buffer = io.BytesIO()
            placeholder.save(buffer, format='JPEG')
            buffer.seek(0)
            return send_file(buffer, mimetype='image/jpeg')
        
        folder_path = os.getenv(IMAGE_FOLDER_ENV)
        if not folder_path:
            # Return placeholder for missing env var
            placeholder = Image.new('RGB', (200, 200), color=(200, 200, 200))
            buffer = io.BytesIO()
            placeholder.save(buffer, format='JPEG')
            buffer.seek(0)
            return send_file(buffer, mimetype='image/jpeg')
        
        folder_path = os.path.expanduser(folder_path)
        full_path = os.path.abspath(os.path.join(folder_path, relative_path))
        
        # Security check: ensure the path is within the folder
        if not full_path.startswith(os.path.abspath(folder_path)):
            # Return placeholder for invalid path
            placeholder = Image.new('RGB', (200, 200), color=(180, 180, 180))
            buffer = io.BytesIO()
            placeholder.save(buffer, format='JPEG')
            buffer.seek(0)
            return send_file(buffer, mimetype='image/jpeg')
        
        if not os.path.isfile(full_path):
            # Return placeholder for missing file
            placeholder = Image.new('RGB', (200, 200), color=(160, 160, 160))
            buffer = io.BytesIO()
            placeholder.save(buffer, format='JPEG')
            buffer.seek(0)
            return send_file(buffer, mimetype='image/jpeg')
        
        # Load and resize image
        img = Image.open(full_path)
        img.thumbnail((200, 200), Image.Resampling.LANCZOS)
        
        # Convert RGBA to RGB for JPEG compatibility
        if img.mode in ('RGBA', 'LA', 'P'):
            # Create white background
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Convert to binary
        buffer = io.BytesIO()
        img.save(buffer, format='JPEG', quality=85)
        buffer.seek(0)
        
        return send_file(buffer, mimetype='image/jpeg')
    except Exception as e:
        logger.error(f"Error getting thumbnail: {e}")
        # Return placeholder for error
        placeholder = Image.new('RGB', (200, 200), color=(100, 100, 100))
        buffer = io.BytesIO()
        placeholder.save(buffer, format='JPEG')
        buffer.seek(0)
        return send_file(buffer, mimetype='image/jpeg')

@plugin_bp.route('/image_folder/delete', methods=['POST'])
def image_folder_delete_image():
    """Delete an image from the folder."""
    try:
        import os
        from plugins.image_folder.image_folder import IMAGE_FOLDER_ENV
        
        data = request.json
        relative_path = data.get('relative_path')
        
        if not relative_path:
            return jsonify({"success": False, "error": "relative_path is required"}), 400
        
        folder_path = os.getenv(IMAGE_FOLDER_ENV)
        if not folder_path:
            return jsonify({"success": False, "error": f"Environment variable {IMAGE_FOLDER_ENV} not set"}), 400
        
        folder_path = os.path.expanduser(folder_path)
        full_path = os.path.abspath(os.path.join(folder_path, relative_path))
        
        # Security check: ensure the path is within the folder
        if not full_path.startswith(os.path.abspath(folder_path)):
            return jsonify({"success": False, "error": "Invalid path"}), 403
        
        if not os.path.isfile(full_path):
            return jsonify({"success": False, "error": f"File not found"}), 404
        
        os.remove(full_path)
        logger.info(f"Deleted image: {full_path}")
        
        return jsonify({
            "success": True,
            "message": f"Image deleted: {relative_path}"
        }), 200
    except Exception as e:
        logger.error(f"Error deleting image: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@plugin_bp.route('/image_folder/upload', methods=['POST'])
def image_folder_upload_image():
    """Upload a new image to the folder."""
    try:
        import os
        from PIL import Image
        from plugins.image_folder.image_folder import IMAGE_FOLDER_ENV
        
        if 'file' not in request.files:
            return jsonify({"success": False, "error": "No file provided"}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({"success": False, "error": "No file selected"}), 400
        
        allowed_extensions = ('avif', 'jpg', 'jpeg', 'png', 'gif', 'bmp', 'tiff', 'webp', 'heif', 'heic')
        
        folder_path = os.getenv(IMAGE_FOLDER_ENV)
        if not folder_path:
            return jsonify({"success": False, "error": f"Environment variable {IMAGE_FOLDER_ENV} not set"}), 400
        
        folder_path = os.path.expanduser(folder_path)
        
        # Check file extension
        filename = file.filename
        if '.' not in filename:
            return jsonify({"success": False, "error": "File must have an extension"}), 400
        
        ext = filename.rsplit('.', 1)[1].lower()
        if ext not in allowed_extensions:
            return jsonify({"success": False, "error": f"File type not allowed. Allowed: {', '.join(allowed_extensions)}"}), 400
        
        # Ensure unique filename if it already exists
        full_path = os.path.join(folder_path, filename)
        if os.path.exists(full_path):
            name, ext_with_dot = os.path.splitext(filename)
            counter = 1
            while os.path.exists(full_path):
                filename = f"{name}_{counter}{ext_with_dot}"
                full_path = os.path.join(folder_path, filename)
                counter += 1
        
        # Validate it's actually an image
        try:
            img = Image.open(file.stream)
            img.verify()
        except Exception as e:
            return jsonify({"success": False, "error": f"Invalid image file: {e}"}), 400
        
        # Save the file
        file.seek(0)  # Reset file pointer after verify
        file.save(full_path)
        logger.info(f"Uploaded image: {full_path}")
        
        size_bytes = os.path.getsize(full_path)
        return jsonify({
            "success": True,
            "message": "Image uploaded successfully",
            "image": {
                'filename': filename,
                'relative_path': os.path.relpath(full_path, folder_path),
                'size_bytes': size_bytes,
                'size_kb': round(size_bytes / 1024, 2)
            }
        }), 200
    except Exception as e:
        logger.error(f"Error uploading image: {e}")
        return jsonify({"success": False, "error": str(e)}), 500



    return jsonify({"success": True, "message": "Display updated"}), 200