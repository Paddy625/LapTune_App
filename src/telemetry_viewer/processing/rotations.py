"""Right-handed SO(3) operations. Vehicle axes: X forward, Y left, Z up."""

import numpy as np

VEHICLE_AXES = "X forward, Y left, Z up"
VEHICLE_FRAME = "FLU"


def skew(v):
    x, y, z = v
    return np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])


def rotation_exp(vector):
    angle = float(np.linalg.norm(vector))
    cross = skew(vector)
    if angle < 1e-7:
        return np.eye(3) + cross + 0.5 * cross @ cross
    return (
        np.eye(3) + np.sin(angle) / angle * cross + (1 - np.cos(angle)) / angle**2 * cross @ cross
    )


def rotation_log(rotation):
    angle = np.arccos(np.clip((np.trace(rotation) - 1) / 2, -1, 1))
    vee = np.array(
        [
            rotation[2, 1] - rotation[1, 2],
            rotation[0, 2] - rotation[2, 0],
            rotation[1, 0] - rotation[0, 1],
        ]
    )
    if angle < 1e-7:
        return vee / 2
    if np.pi - angle < 1e-5:
        # Eigenvector avoids the sin(pi) singularity; choose a consistent sign.
        values, vectors = np.linalg.eigh((rotation + rotation.T) / 2)
        axis = vectors[:, np.argmax(values)]
        if np.dot(axis, vee) < 0:
            axis = -axis
        return axis * angle
    return vee * angle / (2 * np.sin(angle))


def track_rotation(velocity):
    """Zero-bank reference: forward X follows travel, left Y is horizontal."""
    x = velocity / np.linalg.norm(velocity)
    y = np.cross([0.0, 0.0, 1.0], x)
    y /= np.linalg.norm(y)
    return np.column_stack((x, y, np.cross(x, y)))


def attitude_angles(rotation):
    roll = np.arctan2(rotation[2, 1], rotation[2, 2])
    # Preserve the physical front-up pitch readout. With leftward Y, this is
    # opposite the right-hand rotation angle about +Y (and its gyro rate).
    pitch = np.arcsin(np.clip(rotation[2, 0], -1, 1))
    forward = rotation[:, 0]
    heading = np.arctan2(forward[0], forward[1]) % (2 * np.pi)
    return np.degrees([roll, pitch, heading])
