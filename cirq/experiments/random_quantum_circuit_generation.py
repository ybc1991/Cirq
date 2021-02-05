# Copyright 2020 The Cirq Developers
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Code for generating random quantum circuits."""

import dataclasses
from typing import (
    Any,
    Callable,
    Container,
    Dict,
    Iterable,
    List,
    Sequence,
    TYPE_CHECKING,
    Tuple,
    Union,
)

from cirq import circuits, devices, google, ops, protocols, value
from cirq._doc import document

if TYPE_CHECKING:
    import numpy as np
    import cirq


@dataclasses.dataclass(frozen=True)
class GridInteractionLayer(Container[Tuple[devices.GridQubit, devices.GridQubit]]):
    """A layer of aligned or staggered two-qubit interactions on a grid.

    Layers of this type have two different basic structures,
    aligned:

    *-* *-* *-*
    *-* *-* *-*
    *-* *-* *-*
    *-* *-* *-*
    *-* *-* *-*
    *-* *-* *-*

    and staggered:

    *-* *-* *-*
    * *-* *-* *
    *-* *-* *-*
    * *-* *-* *
    *-* *-* *-*
    * *-* *-* *

    Other variants are obtained by offsetting these lattices to the right by
    some number of columns, and/or transposing into the vertical orientation.
    There are a total of 4 aligned and 4 staggered variants.

    The 2x2 unit cells for the aligned and staggered versions of this layer
    are, respectively:

    *-*
    *-*

    and

    *-*
    * *-

    with left/top qubits at (0, 0) and (1, 0) in the aligned case, or
    (0, 0) and (1, 1) in the staggered case. Other variants have the same unit
    cells after transposing and offsetting.

    Args:
        col_offset: Number of columns by which to shift the basic lattice.
        vertical: Whether gates should be oriented vertically rather than
            horizontally.
        stagger: Whether to stagger gates in neighboring rows.
    """

    col_offset: int = 0
    vertical: bool = False
    stagger: bool = False

    def __contains__(self, pair) -> bool:
        """Checks whether a pair is in this layer."""
        if self.vertical:
            # Transpose row, col coords for vertical orientation.
            a, b = pair
            pair = devices.GridQubit(a.col, a.row), devices.GridQubit(b.col, b.row)

        a, b = sorted(pair)

        # qubits should be 1 column apart.
        if (a.row != b.row) or (b.col != a.col + 1):
            return False

        # mod to get the position in the 2 x 2 unit cell with column offset.
        pos = a.row % 2, (a.col - self.col_offset) % 2
        return pos == (0, 0) or pos == (1, self.stagger)

    def _json_dict_(self) -> Dict[str, Any]:
        return protocols.obj_to_dict_helper(self, ['col_offset', 'vertical', 'stagger'])

    def __repr__(self) -> str:
        return (
            'cirq.experiments.GridInteractionLayer('
            f'col_offset={self.col_offset}, '
            f'vertical={self.vertical}, '
            f'stagger={self.stagger})'
        )


GRID_STAGGERED_PATTERN = (
    GridInteractionLayer(col_offset=0, vertical=True, stagger=True),  # A
    GridInteractionLayer(col_offset=1, vertical=True, stagger=True),  # B
    GridInteractionLayer(col_offset=1, vertical=False, stagger=True),  # C
    GridInteractionLayer(col_offset=0, vertical=False, stagger=True),  # D
    GridInteractionLayer(col_offset=1, vertical=False, stagger=True),  # C
    GridInteractionLayer(col_offset=0, vertical=False, stagger=True),  # D
    GridInteractionLayer(col_offset=0, vertical=True, stagger=True),  # A
    GridInteractionLayer(col_offset=1, vertical=True, stagger=True),  # B
)
document(
    GRID_STAGGERED_PATTERN,
    """A pattern of two-qubit gates that is hard to simulate.

    This pattern of gates was used in the paper
    https://www.nature.com/articles/s41586-019-1666-5
    to demonstrate quantum supremacy.
    """,
)

GRID_ALIGNED_PATTERN = (
    GridInteractionLayer(col_offset=0, vertical=False, stagger=False),  # E
    GridInteractionLayer(col_offset=1, vertical=False, stagger=False),  # F
    GridInteractionLayer(col_offset=0, vertical=True, stagger=False),  # G
    GridInteractionLayer(col_offset=1, vertical=True, stagger=False),  # H
)
document(
    GRID_ALIGNED_PATTERN,
    """A pattern of two-qubit gates that is easy to simulate.

    This pattern of gates was used in the paper
    https://www.nature.com/articles/s41586-019-1666-5
    to evaluate the performance of a quantum computer.
    """,
)


def random_rotations_between_two_qubit_circuit(
    q0: 'cirq.Qid',
    q1: 'cirq.Qid',
    depth: int,
    two_qubit_op_factory: Callable[
        ['cirq.Qid', 'cirq.Qid', 'np.random.RandomState'], 'cirq.OP_TREE'
    ] = lambda a, b, _: google.SYC(a, b),
    single_qubit_gates: Sequence['cirq.Gate'] = (
        ops.X ** 0.5,
        ops.Y ** 0.5,
        ops.PhasedXPowGate(phase_exponent=0.25, exponent=0.5),
    ),
    add_final_single_qubit_layer: bool = True,
    seed: 'cirq.RANDOM_STATE_OR_SEED_LIKE' = None,
) -> 'cirq.Circuit':
    """Generate a random two-qubit quantum circuit.

    This construction uses a similar structure to those in the paper
    https://www.nature.com/articles/s41586-019-1666-5.

    The generated circuit consists of a number of "cycles", this number being
    specified by `depth`. Each cycle is actually composed of two sub-layers:
    a layer of single-qubit gates followed by a layer of two-qubit gates,
    controlled by their respective arguments, see below.

    Args:
        q0: The first qubit
        q1: The second qubit
        depth: The number of cycles.
        two_qubit_op_factory: A callable that returns a two-qubit operation.
            These operations will be generated with calls of the form
            `two_qubit_op_factory(q0, q1, prng)`, where `prng` is the
            pseudorandom number generator.
        single_qubit_gates: Single-qubit gates are selected randomly from this
            sequence. No qubit is acted upon by the same single-qubit gate in
            consecutive cycles. If only one choice of single-qubit gate is
            given, then this constraint is not enforced.
        add_final_single_qubit_layer: Whether to include a final layer of
            single-qubit gates after the last cycle (subject to the same
            non-consecutivity constraint).
        seed: A seed or random state to use for the pseudorandom number
            generator.
    """
    prng = value.parse_random_state(seed)

    circuit = circuits.Circuit()
    previous_single_qubit_layer = ops.Moment()
    single_qubit_layer_factory = _single_qubit_gates_arg_to_factory(
        single_qubit_gates=single_qubit_gates, qubits=(q0, q1), prng=prng
    )

    for _ in range(depth):
        single_qubit_layer = single_qubit_layer_factory.new_layer(previous_single_qubit_layer)
        circuit += single_qubit_layer
        circuit += two_qubit_op_factory(q0, q1, prng)
        previous_single_qubit_layer = single_qubit_layer

    if add_final_single_qubit_layer:
        circuit += single_qubit_layer_factory.new_layer(previous_single_qubit_layer)

    return circuit


def random_rotations_between_grid_interaction_layers_circuit(
    qubits: Iterable['cirq.GridQubit'],
    depth: int,
    *,  # forces keyword arguments
    two_qubit_op_factory: Callable[
        ['cirq.GridQubit', 'cirq.GridQubit', 'np.random.RandomState'], 'cirq.OP_TREE'
    ] = lambda a, b, _: google.SYC(a, b),
    pattern: Sequence[GridInteractionLayer] = GRID_STAGGERED_PATTERN,
    single_qubit_gates: Sequence['cirq.Gate'] = (
        ops.X ** 0.5,
        ops.Y ** 0.5,
        ops.PhasedXPowGate(phase_exponent=0.25, exponent=0.5),
    ),
    add_final_single_qubit_layer: bool = True,
    seed: 'cirq.RANDOM_STATE_OR_SEED_LIKE' = None,
) -> 'cirq.Circuit':
    """Generate a random quantum circuit of a particular form.

    This construction is based on the circuits used in the paper
    https://www.nature.com/articles/s41586-019-1666-5.

    The generated circuit consists of a number of "cycles", this number being
    specified by `depth`. Each cycle is actually composed of two sub-layers:
    a layer of single-qubit gates followed by a layer of two-qubit gates,
    controlled by their respective arguments, see below. The pairs of qubits
    in a given entangling layer is controlled by the `pattern` argument,
    see below.

    Args:
        qubits: The qubits to use.
        depth: The number of cycles.
        two_qubit_op_factory: A callable that returns a two-qubit operation.
            These operations will be generated with calls of the form
            `two_qubit_op_factory(q0, q1, prng)`, where `prng` is the
            pseudorandom number generator.
        pattern: A sequence of GridInteractionLayers, each of which determine
            which pairs of qubits are entangled. The layers in a pattern are
            iterated through sequentially, repeating until `depth` is reached.
        single_qubit_gates: Single-qubit gates are selected randomly from this
            sequence. No qubit is acted upon by the same single-qubit gate in
            consecutive cycles. If only one choice of single-qubit gate is
            given, then this constraint is not enforced.
        add_final_single_qubit_layer: Whether to include a final layer of
            single-qubit gates after the last cycle.
        seed: A seed or random state to use for the pseudorandom number
            generator.
    """
    prng = value.parse_random_state(seed)
    qubits = list(qubits)
    coupled_qubit_pairs = _coupled_qubit_pairs(qubits)

    circuit = circuits.Circuit()
    previous_single_qubit_layer = ops.Moment()
    single_qubit_layer_factory = _single_qubit_gates_arg_to_factory(
        single_qubit_gates=single_qubit_gates, qubits=qubits, prng=prng
    )

    for i in range(depth):
        single_qubit_layer = single_qubit_layer_factory.new_layer(previous_single_qubit_layer)
        circuit += single_qubit_layer

        two_qubit_layer = _two_qubit_layer(
            coupled_qubit_pairs, two_qubit_op_factory, pattern[i % len(pattern)], prng
        )
        circuit += two_qubit_layer
        previous_single_qubit_layer = single_qubit_layer

    if add_final_single_qubit_layer:
        circuit += single_qubit_layer_factory.new_layer(previous_single_qubit_layer)

    return circuit


def _coupled_qubit_pairs(
    qubits: List['cirq.GridQubit'],
) -> List[Tuple['cirq.GridQubit', 'cirq.GridQubit']]:
    pairs = []
    qubit_set = set(qubits)
    for qubit in qubits:

        def add_pair(neighbor: 'cirq.GridQubit'):
            if neighbor in qubit_set:
                pairs.append((qubit, neighbor))

        add_pair(devices.GridQubit(qubit.row, qubit.col + 1))
        add_pair(devices.GridQubit(qubit.row + 1, qubit.col))

    return pairs


class _RandomSingleQubitLayerFactory:
    def __init__(
        self,
        qubits: Sequence['cirq.Qid'],
        single_qubit_gates: Sequence['cirq.Gate'],
        prng: 'np.random.RandomState',
    ) -> None:
        self.qubits = qubits
        self.single_qubit_gates = single_qubit_gates
        self.prng = prng

    def new_layer(self, previous_single_qubit_layer: 'cirq.Moment') -> 'cirq.Moment':
        def random_gate(qubit: 'cirq.Qid') -> 'cirq.Gate':
            excluded_op = previous_single_qubit_layer.operation_at(qubit)
            excluded_gate = excluded_op.gate if excluded_op is not None else None
            g = self.single_qubit_gates[self.prng.randint(0, len(self.single_qubit_gates))]
            while g == excluded_gate:
                g = self.single_qubit_gates[self.prng.randint(0, len(self.single_qubit_gates))]
            return g

        return ops.Moment(random_gate(q).on(q) for q in self.qubits)


class _FixedSingleQubitLayerFactory:
    def __init__(self, fixed_single_qubit_layer: Dict['cirq.Qid', 'cirq.Gate']) -> None:
        self.fixed_single_qubit_layer = fixed_single_qubit_layer

    def new_layer(self, previous_single_qubit_layer: 'cirq.Moment') -> 'cirq.Moment':
        return ops.Moment(v.on(q) for q, v in self.fixed_single_qubit_layer.items())


_SingleQubitLayerFactory = Union[_FixedSingleQubitLayerFactory, _RandomSingleQubitLayerFactory]


def _single_qubit_gates_arg_to_factory(
    single_qubit_gates: Sequence['cirq.Gate'], qubits: Sequence['cirq.Qid'], prng: 'np.RandomState'
) -> _SingleQubitLayerFactory:
    """Parse the `single_qubit_gates` argument for circuit generation functions.

    If only one single qubit gate is provided, it will be used everywhere.
    Otherwise, we use the factory that excludes operations that were used
    in the previous layer.
    """
    if len(set(single_qubit_gates)) == 1:
        return _FixedSingleQubitLayerFactory({q: single_qubit_gates[0] for q in qubits})

    return _RandomSingleQubitLayerFactory(qubits, single_qubit_gates, prng)


def _two_qubit_layer(
    coupled_qubit_pairs: List[Tuple['cirq.GridQubit', 'cirq.GridQubit']],
    two_qubit_op_factory: Callable[
        ['cirq.GridQubit', 'cirq.GridQubit', 'np.random.RandomState'], 'cirq.OP_TREE'
    ],
    layer: GridInteractionLayer,
    prng: 'np.random.RandomState',
) -> 'cirq.OP_TREE':
    for a, b in coupled_qubit_pairs:
        if (a, b) in layer:
            yield two_qubit_op_factory(a, b, prng)
