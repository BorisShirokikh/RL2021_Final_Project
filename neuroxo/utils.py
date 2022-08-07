import os
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch

import neuroxo


def get_random_field(n=10, min_depth=0, max_depth=10):
    idxs = np.random.permutation(np.arange(n * n))
    depth = np.random.randint(low=min_depth, high=max_depth)
    split_idx = depth // 2 + depth % 2
    xs = idxs[:split_idx]
    os = idxs[split_idx:depth]
    field = np.zeros((n, n), dtype='float32')
    field.ravel()[xs] = 1
    field.ravel()[os] = -1
    return field


def get_repo_root():
    return Path(neuroxo.__file__).parent.parent


def flush(*args, **kwargs):
    kwargs['flush'] = True
    print(*args, **kwargs)


def np_rand_argmax(a):
    return np.random.choice(np.where(a == a.max())[0])


def update_lr(optimizer: torch.optim.Optimizer, epoch2lr: dict, epoch: int):
    try:
        optimizer.param_groups[0]['lr'] = epoch2lr[epoch]
    except (KeyError, TypeError):  # (no such epochs in the dict, dict is None)
        pass


def s2hhmmss(s: float):
    s = int(s)
    hh = s // 3600
    mm = (s % 3600) // 60
    ss = s % 60
    return f'{hh:03d}:{mm:02d}:{ss:02d}'
