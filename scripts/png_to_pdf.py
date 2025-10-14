import sys
import struct
import zlib
from pathlib import Path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def read_chunk(stream):
    length_data = stream.read(4)
    if len(length_data) < 4:
        return None, None
    length = struct.unpack(">I", length_data)[0]
    chunk_type = stream.read(4)
    chunk_data = stream.read(length)
    stream.read(4)  # CRC, ignored for conversion
    return chunk_type, chunk_data


def paeth_predictor(a, b, c):
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def reconstruct_scanlines(data, width, height, bpp):
    row_bytes = width * bpp
    i = 0
    prev_row = bytearray(row_bytes)
    output = bytearray()
    for _ in range(height):
        filter_type = data[i]
        i += 1
        row = bytearray(row_bytes)
        for j in range(row_bytes):
            raw = data[i]
            i += 1
            if filter_type == 0:  # None
                val = raw
            elif filter_type == 1:  # Sub
                left = row[j - bpp] if j >= bpp else 0
                val = (raw + left) & 0xFF
            elif filter_type == 2:  # Up
                up = prev_row[j]
                val = (raw + up) & 0xFF
            elif filter_type == 3:  # Average
                left = row[j - bpp] if j >= bpp else 0
                up = prev_row[j]
                val = (raw + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:  # Paeth
                left = row[j - bpp] if j >= bpp else 0
                up = prev_row[j]
                up_left = prev_row[j - bpp] if j >= bpp else 0
                val = (raw + paeth_predictor(left, up, up_left)) & 0xFF
            else:
                raise ValueError(f"Unsupported PNG filter: {filter_type}")
            row[j] = val
        output.extend(row)
        prev_row = row
    return bytes(output)


def convert_png_to_pdf(png_path, pdf_path):
    png_path = Path(png_path)
    pdf_path = Path(pdf_path)
    with png_path.open('rb') as f:
        signature = f.read(len(PNG_SIGNATURE))
        if signature != PNG_SIGNATURE:
            raise ValueError("El archivo no es un PNG válido")
        width = height = bit_depth = color_type = None
        idat_parts = []
        while True:
            chunk_type, chunk_data = read_chunk(f)
            if chunk_type is None:
                break
            if chunk_type == b'IHDR':
                width, height, bit_depth, color_type = struct.unpack(">IIBB", chunk_data[:10])
            elif chunk_type == b'IDAT':
                idat_parts.append(chunk_data)
            elif chunk_type == b'IEND':
                break

    if width is None or height is None:
        raise ValueError("Cabecera PNG incompleta")
    if bit_depth != 8:
        raise ValueError("Solo se admiten imágenes de 8 bits por canal")
    if color_type not in (2, 6):
        raise ValueError("Solo se admiten imágenes RGB o RGBA")

    compressed = b''.join(idat_parts)
    decompressed = zlib.decompress(compressed)
    channels = 3 if color_type == 2 else 4
    bpp = channels
    raw = reconstruct_scanlines(decompressed, width, height, bpp)

    if color_type == 6:
        rgb_bytes = bytearray()
        for i in range(0, len(raw), 4):
            rgb_bytes.extend(raw[i:i+3])
        raw = bytes(rgb_bytes)

    image_stream = zlib.compress(raw)
    objects = []

    def add_object(content):
        objects.append(content)
        return len(objects)

    catalog_id = add_object(b"<< /Type /Catalog /Pages 2 0 R >>")
    pages_id = add_object(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    page_dict = f"<< /Type /Page /Parent 2 0 R /Resources << /XObject << /Im0 4 0 R >> /ProcSet [/PDF /ImageC] >> /MediaBox [0 0 {width} {height}] /Contents 5 0 R >>".encode()
    page_id = add_object(page_dict)
    image_dict = f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length {len(image_stream)} >>".encode()
    image_obj = image_dict + b"\nstream\n" + image_stream + b"\nendstream"
    image_id = add_object(image_obj)
    content_stream = f"q {width} 0 0 {height} 0 0 cm /Im0 Do Q".encode()
    content_dict = f"<< /Length {len(content_stream)} >>".encode() + b"\nstream\n" + content_stream + b"\nendstream"
    content_id = add_object(content_dict)

    offsets = []
    pdf_bytes = bytearray(b"%PDF-1.4\n")
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(pdf_bytes))
        pdf_bytes.extend(f"{idx} 0 obj\n".encode())
        pdf_bytes.extend(obj)
        pdf_bytes.extend(b"\nendobj\n")

    xref_offset = len(pdf_bytes)
    pdf_bytes.extend(f"xref\n0 {len(objects)+1}\n".encode())
    pdf_bytes.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        pdf_bytes.extend(f"{offset:010} 00000 n \n".encode())
    pdf_bytes.extend(b"trailer\n")
    pdf_bytes.extend(f"<< /Size {len(objects)+1} /Root 1 0 R >>\n".encode())
    pdf_bytes.extend(b"startxref\n")
    pdf_bytes.extend(f"{xref_offset}\n".encode())
    pdf_bytes.extend(b"%%EOF")

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(pdf_bytes)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Uso: python png_to_pdf.py <entrada.png> <salida.pdf>")
        sys.exit(1)
    convert_png_to_pdf(sys.argv[1], sys.argv[2])
