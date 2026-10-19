from implementation import transform

for value in (-5, 0, 1, 10):
    assert transform(value) == abs(value)
