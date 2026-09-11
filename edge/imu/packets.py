"""Vendor-agnostic IMU packet. Not a frozen-shoulder classifier."""
import json
import math


def lost_packet(source, placement='arm', error='imu_lost'):
    return {
        'ok': False,
        'lost': True,
        'source': source,
        'placement': placement,
        'simulation': source == 'simulation',
        'ax': None, 'ay': None, 'az': None,
        'gx': None, 'gy': None, 'gz': None,
        'accel_norm': None,
        'gyro_norm': None,
        'error': error,
    }


def packet(source, placement, ax, ay, az, gx, gy, gz, timestamp=None):
    accel = (float(ax), float(ay), float(az))
    gyro = (float(gx), float(gy), float(gz))
    return {
        'ok': True,
        'lost': False,
        'source': source,
        'placement': placement,
        'simulation': source == 'simulation',
        'ax': round(accel[0], 4), 'ay': round(accel[1], 4), 'az': round(accel[2], 4),
        'gx': round(gyro[0], 3), 'gy': round(gyro[1], 3), 'gz': round(gyro[2], 3),
        'accel_norm': round(math.sqrt(sum(v * v for v in accel)), 4),
        'gyro_norm': round(math.sqrt(sum(v * v for v in gyro)), 3),
        'timestamp': timestamp,
        'error': None,
    }


def parse_line(raw, source='live', default_placement='arm'):
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8', errors='replace')
    text = raw.strip()
    if not text:
        raise ValueError('Empty IMU packet')
    if text.startswith('{'):
        data = json.loads(text)
        return packet(
            source, data.get('placement', default_placement),
            data['ax'], data['ay'], data['az'], data['gx'], data['gy'], data['gz'],
            data.get('t') or data.get('timestamp'),
        )
    parts = [p.strip() for p in text.replace(',', ' ').split() if p.strip()]
    if len(parts) < 6:
        raise ValueError('IMU CSV needs ax ay az gx gy gz')
    values = [float(p) for p in parts[:6]]
    placement = default_placement if len(parts) < 7 else parts[6]
    return packet(source, placement, *values)
