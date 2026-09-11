"""Open the IMU that matches the camera source. Live never receives a synthetic IMU."""
from edge.imu.simulation import SimulatedArmImu


def resolve_transport(camera_source, transport='auto', serial=''):
    transport = (transport or 'auto').lower()
    if camera_source == 'simulation':
        return 'off' if transport == 'off' else 'simulation'
    if transport == 'simulation':
        raise ValueError('Simulated IMU cannot attach to a live RealSense session')
    if transport in ('udp', 'serial', 'off'):
        return transport
    if serial:
        return 'serial'
    return 'off'


def describe_imu(camera_source='simulation', transport='auto', serial='', required=False, placements=('arm',)):
    try:
        resolved = resolve_transport(camera_source, transport, serial)
    except ValueError:
        resolved = 'invalid'
    return {
        'placements': list(placements),
        'transport': resolved,
        'required': required,
        'live_available': camera_source == 'live' and resolved in ('udp', 'serial'),
        'role': 'kinematics_and_quality_not_diagnosis',
        'camera': camera_source,
    }


def open_imu(camera_source, transport='auto', udp_host='127.0.0.1', udp_port=8766,
             serial='', baud=115200, placements=('arm',)):
    resolved = resolve_transport(camera_source, transport, serial)
    placement = placements[0] if placements else 'arm'
    if resolved == 'off':
        return None
    if resolved == 'simulation':
        return SimulatedArmImu(placement)
    if resolved == 'udp':
        from edge.imu.readers import UdpImu
        return UdpImu(udp_host, udp_port, placement)
    if resolved == 'serial':
        if not serial:
            raise ValueError('REHABAI_IMU_SERIAL is required for serial IMU transport')
        from edge.imu.readers import SerialImu
        return SerialImu(serial, baud, placement)
    raise ValueError('Unknown IMU transport: ' + resolved)


def read_imu(device, timestamp, camera_source, angle=None, side='right', drop=False):
    if device is None:
        return None
    if camera_source == 'simulation':
        return device.read_from_angle(timestamp, 0.0 if angle is None else angle, side, drop=drop)
    return device.read(timestamp)
