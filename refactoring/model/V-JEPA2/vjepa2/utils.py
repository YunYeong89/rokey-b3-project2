# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""upstream src/masks/utils.py의 apply_masks + timm drop_path 대체 + torch 내장 trunc_normal_."""

import torch
from torch.nn.init import trunc_normal_  # noqa: F401  (초기화 전용, 사전학습 가중치를 덮어씀)


def apply_masks(x, masks, concat=True):
    """x (B, N, D)에서 masks [(B, K), ...]의 patch 인덱스만 남깁니다."""
    all_x = [torch.gather(x, dim=1, index=m.unsqueeze(-1).repeat(1, 1, x.size(-1))) for m in masks]
    return torch.cat(all_x, dim=0) if concat else all_x


def drop_path(x, drop_prob=0.0, training=False):
    """샘플 단위 stochastic depth (timm.models.layers.drop_path와 같은 동작). 추론에서는 항등."""
    if drop_prob == 0.0 or not training:
        return x
    keep = 1 - drop_prob
    return x * x.new_empty((x.shape[0],) + (1,) * (x.ndim - 1)).bernoulli_(keep) / keep
