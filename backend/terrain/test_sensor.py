import numpy as np
import pytest

from backend.terrain.sensor import ConfusionSensor, confusion_from_diag

CLASSES = ["grass", "paved", "water"]


def test_confusion_from_diag_is_row_stochastic():
    m = confusion_from_diag(3, 0.8)
    assert np.allclose(m.sum(axis=1), 1.0)
    assert np.allclose(np.diag(m), 0.8)
    assert np.allclose(m[0, 1], 0.1)


def test_identity_sensor_is_one_hot():
    s = ConfusionSensor(np.eye(3), CLASSES)
    for c in range(3):
        p = s.read(c)
        assert p[c] == 1.0 and p.sum() == 1.0 and s.last_reading == c


def test_posterior_is_bayes_over_the_column_for_the_drawn_reading():
    m = confusion_from_diag(3, 0.7)
    s = ConfusionSensor(m, CLASSES, seed=1)
    p = s.read(0)
    col = m[:, s.last_reading]
    assert np.allclose(p, col / col.sum())


def test_reset_replays_the_same_draws():
    s = ConfusionSensor(confusion_from_diag(3, 0.6), CLASSES, seed=7)
    a = [s.last_reading for _ in range(20) if s.read(0) is not None]
    s.reset()
    b = [s.last_reading for _ in range(20) if s.read(0) is not None]
    assert a == b and len(set(a)) > 1


def test_rejects_a_matrix_that_is_not_row_stochastic():
    with pytest.raises(ValueError):
        ConfusionSensor(np.ones((3, 3)), CLASSES)


def test_describe_is_stamped_simulated():
    d = ConfusionSensor(confusion_from_diag(3, 0.9), CLASSES, footprint_m=0.5).describe()
    assert d["source"] == "simulated" and d["k"] == 3
    assert d["confusion_diag"] == pytest.approx(0.9) and d["footprint_m"] == 0.5
