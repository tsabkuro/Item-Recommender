import pytest


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


@pytest.fixture
def clock():
    return FakeClock()
