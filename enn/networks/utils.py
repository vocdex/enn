# pylint: disable=g-bad-file-header
# Copyright 2021 DeepMind Technologies Limited. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or  implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ============================================================================
"""Utility functions for networks."""
from typing import Callable, Optional, Tuple

import chex
from enn import base
from enn.networks import base as network_base
import haiku as hk
import jax.numpy as jnp


def parse_net_output(net_out: base.Output) -> chex.Array:
  """Convert network output to scalar prediction value."""
  if isinstance(net_out, base.OutputWithPrior):
    return net_out.preds
  else:
    return net_out


def parse_to_output_with_prior(
    net_out: base.Output) -> base.OutputWithPrior:
  """Convert network output to base.OutputWithPrior."""
  if isinstance(net_out, base.OutputWithPrior):
    return net_out
  else:
    return base.OutputWithPrior(
        train=net_out, prior=jnp.zeros_like(net_out))


def epistemic_network_from_module(
    enn_ctor: Callable[[], network_base.EpistemicModule],
    indexer: base.EpistemicIndexer,
) -> network_base.EpistemicNetworkWithState:
  """Convert an Enn module to epistemic network with paired index."""

  def enn_fn(inputs: chex.Array,
             index: base.Index) -> base.Output:
    return enn_ctor()(inputs, index)

  transformed = hk.without_apply_rng(hk.transform_with_state(enn_fn))
  return network_base.EpistemicNetworkWithState(transformed.apply,
                                                transformed.init, indexer)


def wrap_transformed_as_enn(
    transformed: hk.Transformed) -> network_base.EpistemicNetwork:
  """Wraps a simple transformed function y = f(x) as an ENN."""
  return network_base.EpistemicNetwork(
      apply=lambda params, x, z: transformed.apply(params, x),
      init=lambda key, x, z: transformed.init(key, x),
      indexer=lambda key: key,
  )


def wrap_transformed_as_enn_with_state(
    transformed: hk.Transformed
) -> network_base.EpistemicNetworkWithState:
  """Wraps a simple transformed function y = f(x) as an ENN."""
  apply = lambda params, x, z: transformed.apply(params, x)
  apply = wrap_apply_as_apply_with_state(apply)
  init = lambda key, x, z: transformed.init(key, x)
  init = wrap_init_as_init_with_state(init)
  return network_base.EpistemicNetworkWithState(
      apply=apply,
      init=init,
      indexer=lambda key: key,
  )


def wrap_enn_as_enn_with_state(
    enn: network_base.EpistemicNetwork
) -> network_base.EpistemicNetworkWithState:
  """Wraps a standard ENN as an ENN with a dummy network state."""

  return network_base.EpistemicNetworkWithState(
      apply=wrap_apply_as_apply_with_state(enn.apply),
      init=wrap_init_as_init_with_state(enn.init),
      indexer=enn.indexer,
  )


def wrap_enn_with_state_as_enn(
    enn: network_base.EpistemicNetworkWithState,
    constant_state: Optional[hk.State] = None,
) -> network_base.EpistemicNetwork:
  """Passes a dummy state to ENN with state as an ENN."""
  if constant_state is None:
    constant_state = {}

  def init(key: chex.PRNGKey, x: chex.Array,
           z: base.Index) -> hk.Params:
    params, unused_state = enn.init(key, x, z)
    return params

  def apply(params: hk.Params, x: chex.Array,
            z: base.Index) -> base.Output:
    output, unused_state = enn.apply(params, constant_state, x, z)
    return output

  return network_base.EpistemicNetwork(
      apply=apply,
      init=init,
      indexer=enn.indexer,
  )


def wrap_apply_as_apply_with_state(
    apply: network_base.ApplyFn,) -> network_base.ApplyFnWithState:
  """Wraps a legacy enn apply as an apply for enn with state."""
  def new_apply(
      params: hk.Params,
      unused_state: hk.State,
      inputs: chex.Array,
      index: base.Index,
  ) -> Tuple[base.Output, hk.State]:
    return (apply(params, inputs, index), {})
  return new_apply


def wrap_init_as_init_with_state(
    init: network_base.InitFn) -> network_base.InitFnWithState:
  """Wraps a legacy enn init as an init for enn with state."""

  def new_init(
      key: chex.PRNGKey,
      inputs: chex.Array,
      index: base.Index,
  ) -> Tuple[hk.Params, hk.State]:
    return (init(key, inputs, index), {})
  return new_init


def scale_enn_output(
    enn: network_base.EpistemicNetworkWithState,
    scale: float,
) -> network_base.EpistemicNetworkWithState:
  """Returns an ENN with output scaled by a scaling factor."""
  def scaled_apply(
      params: hk.Params, state: hk.State, inputs: chex.Array,
      index: base.Index) -> Tuple[base.Output, hk.State]:
    out, state = enn.apply(params, state, inputs, index)
    if isinstance(out, base.OutputWithPrior):
      scaled_out = base.OutputWithPrior(
          train=out.train * scale,
          prior=out.prior * scale,
          extra=out.extra,
      )
    else:
      scaled_out = out * scale
    return scaled_out, state

  return network_base.EpistemicNetworkWithState(
      apply=scaled_apply,
      init=enn.init,
      indexer=enn.indexer,
  )


def make_centered_enn(
    enn: network_base.EpistemicNetwork,
    x_train: chex.Array) -> network_base.EpistemicNetwork:
  """Returns an ENN that centers input according to x_train."""
  assert x_train.ndim > 1  # need to include a batch dimension
  x_mean = jnp.mean(x_train, axis=0)
  x_std = jnp.std(x_train, axis=0)
  def centered_apply(params: hk.Params, x: chex.Array,
                     z: base.Index) -> base.Output:
    normalized_x = (x - x_mean) / (x_std + 1e-9)
    return enn.apply(params, normalized_x, z)

  return network_base.EpistemicNetwork(centered_apply, enn.init, enn.indexer)


def make_centered_enn_with_state(
    enn: network_base.EpistemicNetworkWithState,
    x_train: chex.Array) -> network_base.EpistemicNetworkWithState:
  """Returns an ENN that centers input according to x_train."""
  assert x_train.ndim > 1  # need to include a batch dimension
  x_mean = jnp.mean(x_train, axis=0)
  x_std = jnp.std(x_train, axis=0)
  def centered_apply(params: hk.Params, state: hk.State, x: chex.Array,
                     z: base.Index) -> base.Output:
    normalized_x = (x - x_mean) / (x_std + 1e-9)
    return enn.apply(params, state, normalized_x, z)

  return network_base.EpistemicNetworkWithState(centered_apply, enn.init,
                                                enn.indexer)
