import logging
import os
import socket
import subprocess

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps
import qrcode

logger = logging.getLogger(__name__)

FONT_FAMILIES = {
    "Dogica": [{
        "font-weight": "normal",
        "file": "dogicapixel.ttf"
    },{
        "font-weight": "bold",
        "file": "dogicapixelbold.ttf"
    }],
    "Jost": [{
        "font-weight": "normal",
        "file": "Jost.ttf"
    },{
        "font-weight": "bold",
        "file": "Jost-SemiBold.ttf"
    }],
    "Napoli": [{
        "font-weight": "normal",
        "file": "Napoli.ttf"
    }],
    "DS-Digital": [{
        "font-weight": "normal",
        "file": os.path.join("DS-DIGI", "DS-DIGI.TTF")
    }]
}

FONTS = {
    "ds-gigi": "DS-DIGI.TTF",
    "napoli": "Napoli.ttf",
    "jost": "Jost.ttf",
    "jost-semibold": "Jost-SemiBold.ttf"
}

def resolve_path(file_path):
    src_dir = os.getenv("SRC_DIR")
    if src_dir is None:
        # Default to the src directory
        src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    src_path = Path(src_dir)
    return str(src_path / file_path)

def get_ip_address():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("8.8.8.8", 80))
        ip_address = s.getsockname()[0]
    return ip_address

def get_wifi_name():
    try:
        output = subprocess.check_output(['iwgetid', '-r']).decode('utf-8').strip()
        return output
    except subprocess.CalledProcessError:
        return None

def is_connected():
    """Check if the Raspberry Pi has an internet connection."""
    try:
        # Try to connect to Google's public DNS server
        socket.create_connection(("8.8.8.8", 53), timeout=2)
        return True
    except OSError:
        return False

def get_font(font_name, font_size=50, font_weight="normal"):
    if font_name in FONT_FAMILIES:
        font_variants = FONT_FAMILIES[font_name]

        font_entry = next((entry for entry in font_variants if entry["font-weight"] == font_weight), None)
        if font_entry is None:
            font_entry = font_variants[0]  # Default to first available variant

        if font_entry:
            font_path = resolve_path(os.path.join("static", "fonts", font_entry["file"]))
            return ImageFont.truetype(font_path, font_size)
        else:
            logger.warning(f"Requested font weight not found: font_name={font_name}, font_weight={font_weight}")
    else:
        logger.warning(f"Requested font not found: font_name={font_name}")

    return None

def get_fonts():
    fonts_list = []
    for font_family, variants in FONT_FAMILIES.items():
        for variant in variants:
            fonts_list.append({
                "font_family": font_family,
                "url": resolve_path(os.path.join("static", "fonts", variant["file"])),
                "font_weight": variant.get("font-weight", "normal"),
                "font_style": variant.get("font-style", "normal"),
            })
    return fonts_list

def get_font_path(font_name):
    return resolve_path(os.path.join("static", "fonts", FONTS[font_name]))

def generate_startup_image(port,dimensions=(800, 480)):
    bg_color = (255, 255, 255)
    text_color = (0, 0, 0)
    width, height = dimensions
    image_path = resolve_path(os.path.join("static", "images", "patripi.png"))
    
    base_img = Image.open(image_path).convert("RGBA")
    base_img.thumbnail((width, height * 0.85))
    img_w, img_h = base_img.size

    canvas = Image.new("RGBA", dimensions, bg_color)
    
    offset_x = (width - img_w) // 2
    offset_y = (height // 50) 
    canvas.paste(base_img, (offset_x, offset_y), base_img)

    image_draw = ImageDraw.Draw(canvas)
    hostname = socket.gethostname()
    ip = get_ip_address()

    qr_url = f"http://{ip}:{port}"
    qr = qrcode.QRCode(box_size=10, border=2)
    qr.add_data(qr_url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
    
    qr_side = int(height * 0.15)
    qr_img = qr_img.resize((qr_side, qr_side))

    qr_offset_x = (width - qr_side)
    qr_offset_y = height - qr_side 

    canvas.paste(qr_img, (qr_offset_x, qr_offset_y), qr_img)

    text_font_size = int(width * 0.026)
    font = get_font("Jost", text_font_size)

    text_start_y = offset_y + img_h + 20 
    
    instruction = f"FELICES 33 PATRICHU!"
    ip_text = f"Visita http://{hostname}.local o http://{ip}"

    image_draw.text((width/2, text_start_y), instruction, anchor="mm", fill=text_color, font=font)
    
    bbox = image_draw.textbbox((0, 0), instruction, font=font)
    line_height = bbox[3] - bbox[1]
    
    image_draw.text((width/2, text_start_y + line_height + 10), ip_text, anchor="mm", fill=text_color, font=font)

    return canvas

def parse_form(request_form):
    request_dict = request_form.to_dict()
    for key in request_form.keys():
        if key.endswith('[]'):
            request_dict[key] = request_form.getlist(key)
    return request_dict

def handle_request_files(request_files, form_data={}):
    allowed_file_extensions = {'pdf', 'png', 'avif', 'jpg', 'jpeg', 'gif', 'webp', 'heif', 'heic'}
    file_location_map = {}
    # handle existing file locations being provided as part of the form data
    for key in set(request_files.keys()):
        is_list = key.endswith('[]')
        if key in form_data:
            file_location_map[key] = form_data.getlist(key) if is_list else form_data.get(key)
    # add new files in the request
    for key, file in request_files.items(multi=True):
        is_list = key.endswith('[]')
        file_name = file.filename
        if not file_name:
            continue

        extension = os.path.splitext(file_name)[1].replace('.', '')
        if not extension or extension.lower() not in allowed_file_extensions:
            continue

        file_name = os.path.basename(file_name)

        file_save_dir = resolve_path(os.path.join("static", "images", "saved"))
        file_path = os.path.join(file_save_dir, file_name)

        # Open the image and apply EXIF transformation before saving
        if extension in {'jpg', 'jpeg'}:
            try:
                with Image.open(file) as img:
                    img = ImageOps.exif_transpose(img)
                    img.save(file_path)
            except Exception as e:
                logger.warning(f"EXIF processing error for {file_name}: {e}")
                file.save(file_path)
        else:
            # Directly save non-JPEG files
            file.save(file_path)

        if is_list:
            file_location_map.setdefault(key, [])
            file_location_map[key].append(file_path)
        else:
            file_location_map[key] = file_path
    return file_location_map
