import threading
import unittest

import mock
import numpy
import six

import chainer
from chainer import cuda
from chainer import testing
from chainer.testing import attr
from chainer.utils import type_check


class TestFunctionNode(unittest.TestCase):

    def _get_method(self, prefix, gpu):
        suffix = 'gpu' if gpu else 'cpu'
        return getattr(self.f, prefix + '_' + suffix)

    def setUp(self):
        y1 = numpy.arange(4).astype(numpy.float32)
        y2 = numpy.arange(4).astype(numpy.float32) + 1
        gx1 = chainer.Variable(numpy.arange(3).astype(numpy.float32))
        gx2 = None
        gy1 = numpy.arange(4).astype(numpy.float32)
        gy2 = numpy.arange(4).astype(numpy.float32)

        f = chainer.FunctionNode()
        f.check_type_forward = mock.MagicMock()
        f.forward_cpu = mock.MagicMock(return_value=(y1, y2))
        f.forward_gpu = mock.MagicMock()
        f.backward = mock.MagicMock(return_value=(gx1, gx2))
        self.f = f

        self.x1 = numpy.arange(3).astype(numpy.float32)
        self.x2 = numpy.arange(3).astype(numpy.int32)
        self.y1 = y1
        self.y2 = y2
        self.gx1 = gx1
        self.gx2 = gx2
        self.gx1_orig = chainer.Variable(
            numpy.arange(3, 6).astype(numpy.float32))
        self.gx2_orig = chainer.Variable(
            numpy.arange(2, 5).astype(numpy.float32))
        self.gx1_accum = gx1 + self.gx1_orig
        self.gy1 = gy1
        self.gy2 = gy2

    def tearDown(self):
        # Set None to delete cuda array
        self.f = None
        self.y1 = None
        self.y2 = None
        self.gx1 = None

    def setup_gpu(self):
        self.x1 = cuda.to_gpu(self.x1)
        self.x2 = cuda.to_gpu(self.x2)
        self.y1 = cuda.to_gpu(self.y1)
        self.y2 = cuda.to_gpu(self.y2)
        self.gx1.to_gpu()
        self.gx1_orig.to_gpu()
        self.gx2_orig.to_gpu()
        self.gx1_accum.to_gpu()
        self.gy1 = cuda.to_gpu(self.gy1)
        self.gy2 = cuda.to_gpu(self.gy2)
        self.f.forward_gpu = mock.MagicMock(return_value=(self.y1, self.y2))
        self.f.backward = mock.MagicMock(return_value=(self.gx1, self.gx2))

    def check_forward(self, gpu):
        y1, y2 = self.f.forward((self.x1, self.x2))
        self.assertEqual(self.f.check_type_forward.call_count, 0)
        self.assertEqual(self._get_method('forward', not gpu).call_count, 0)
        self._get_method('forward', gpu).assert_called_once_with(
            (self.x1, self.x2))
        self.assertTrue((cuda.to_cpu(y1) == cuda.to_cpu(self.y1)).all())
        self.assertTrue((cuda.to_cpu(y2) == cuda.to_cpu(self.y2)).all())

    def test_forward_cpu(self):
        self.check_forward(False)

    @attr.gpu
    def test_forward_gpu(self):
        self.setup_gpu()
        self.check_forward(True)

    def check_backward_accumulate(self, gxs):
        x1 = chainer.Variable(self.x1)
        x2 = chainer.Variable(self.x2)
        self.f.inputs = (x1.node, x2.node)
        gx1, gx2 = self.f.backward_accumulate(
            (0, 1), (self.gy1, self.gy2), gxs)
        if gxs[0] is None:
            numpy.testing.assert_array_equal(cuda.to_cpu(gx1.data),
                                             cuda.to_cpu(self.gx1.data))
            self.assertIsNone(gx2)
        else:
            numpy.testing.assert_array_equal(cuda.to_cpu(gx1.data),
                                             cuda.to_cpu(self.gx1_accum.data))
            numpy.testing.assert_array_equal(cuda.to_cpu(gx2.data),
                                             cuda.to_cpu(self.gx2_orig.data))

    def test_backward_accumulate_none_cpu(self):
        self.check_backward_accumulate((None, None))

    @attr.gpu
    def test_backward_accumulate_none_gpu(self):
        self.setup_gpu()
        self.check_backward_accumulate((None, None))

    def test_backward_accumulate_cpu(self):
        self.check_backward_accumulate((self.gx1_orig, self.gx2_orig))

    @attr.gpu
    def test_backward_accumulate_gpu(self):
        self.setup_gpu()
        self.check_backward_accumulate((self.gx1_orig, self.gx2_orig))

    def check_check_type_forward(self):
        self.assertEqual(self.f.check_type_forward.call_count, 1)
        ts = self.f.check_type_forward.call_args[0][0]
        self.assertIsInstance(ts, type_check.LightTypeInfoTuple)
        self.assertEqual(len(ts), 2)

        t1 = ts[0]
        self.assertEqual(t1.shape, (3,))
        self.assertEqual(t1.dtype, numpy.float32)

        t2 = ts[1]
        self.assertEqual(t2.shape, (3,))
        self.assertEqual(t2.dtype, numpy.int32)

    def check_apply(self):
        x1 = chainer.Variable(self.x1)
        x2 = chainer.Variable(self.x2)
        x1._node._rank = 1
        x2._node._rank = 3
        ys = self.f.apply((x1, x2))

        self.assertEqual(len(ys), 2)
        self.check_check_type_forward()

        for y in ys:
            self.assertIsInstance(y, chainer.Variable)
            # rank is (maximum rank in xs) + 1
            self.assertEqual(y.rank, 4)
            self.assertIs(y.creator_node, self.f)
            self.assertTrue(y.requires_grad)

        self.assertIsInstance(y.creator_node.outputs, tuple)

    def test_apply_cpu(self):
        self.check_apply()

    @attr.gpu
    def test_apply_gpu(self):
        self.setup_gpu()
        self.check_apply()

    def check_apply_all_ndarray(self):
        x1 = self.x1
        x2 = self.x2
        ys = self.f.apply((x1, x2))

        self.assertEqual(len(ys), 2)
        self.check_check_type_forward()

        for y in ys:
            self.assertIsInstance(y, chainer.Variable)
            self.assertIsInstance(y.data, type(x1))
            self.assertFalse(y.requires_grad)

    def test_apply_all_ndarray_cpu(self):
        self.check_apply_all_ndarray()

    @attr.gpu
    def test_apply_all_ndarray_gpu(self):
        self.setup_gpu()
        self.check_apply_all_ndarray()

    def check_apply_ndarray(self):
        x1 = chainer.Variable(self.x1)
        x2 = self.x2
        x1._node._rank = 1
        ys = self.f.apply((x1, x2))

        self.assertEqual(len(ys), 2)
        self.check_check_type_forward()

        for y in ys:
            self.assertIsInstance(y, chainer.Variable)
            # rank is (maximum rank in xs) + 1
            self.assertEqual(y.rank, 2)
            self.assertIs(y.creator_node, self.f)
            self.assertTrue(y.requires_grad)

        self.assertIsInstance(y.creator_node.outputs, tuple)

    def test_apply_ndarray_cpu(self):
        self.check_apply_ndarray()

    @attr.gpu
    def test_apply_ndarray_gpu(self):
        self.setup_gpu()
        self.check_apply_ndarray()

    def check_apply_single_return_value(self):
        x1 = chainer.Variable(self.x1)
        x2 = chainer.Variable(self.x2)
        ret, = self.f.apply((x1, x2))
        self.assertIsInstance(ret, chainer.Variable)

    def test_apply_single_return_value_cpu(self):
        self.f.forward_cpu.return_value = (cuda.to_cpu(self.y1),)
        self.check_apply_single_return_value()

    @attr.gpu
    def test_apply_single_return_value_gpu(self):
        self.setup_gpu()
        self.f.forward_gpu.return_value = (cuda.to_gpu(self.y1),)
        self.check_apply_single_return_value()

    def _get_f(self):
        x1 = chainer.Variable(self.x1)
        x2 = chainer.Variable(self.x2)
        y1, y2 = self.f.apply((x1, x2))

        f = y1.creator_node
        # To test weak refernece, return only x1 and y1.
        # x2 and y2 are deleted by the garbage collector
        return f, x1, y1

    def test_unchain(self):
        f, _x1, _y1 = self._get_f()
        y1, y2 = f.outputs
        f.unchain()

        # As _y1 is alive, this weak ref is also alive
        y1_ref = y1()
        self.assertIsNotNone(y1_ref)
        self.assertIsNone(y1_ref.creator)
        # This weak ref is dead by unchain
        y2_ref = y2()
        self.assertIsNone(y2_ref)

        self.assertIsNone(f.inputs)

    def test_label(self):
        self.assertEqual(self.f.label, 'FunctionNode')


class TestFunctionNodeInvalidType(unittest.TestCase):

    def test_forward_invalid1(self):
        class FunctionNode(chainer.FunctionNode):

            def check_type_forward(self, in_types):
                x_type, = in_types
                type_check.expect(
                    x_type.dtype == numpy.float32,
                    x_type.ndim >= 2,
                )

            def forward(self, inputs):
                return inputs

        f = FunctionNode()

        # OK
        v = chainer.Variable(numpy.random.randn(1, 5).astype(numpy.float32))
        result, = f.apply((v,))
        assert isinstance(result, chainer.Variable)

        # Incorrect dtype
        # in py3, numpy dtypes are represented as class
        msg = """\
Invalid operation is performed in: FunctionNode \\(Forward\\)

Expect: in_types\\[0\\]\\.dtype == <(type|class) 'numpy\\.float32'>
Actual: float64 \\!= <(type|class) 'numpy\\.float32'>"""

        v = chainer.Variable(numpy.random.randn(1, 5))
        with six.assertRaisesRegex(self, chainer.utils.type_check.InvalidType,
                                   msg):
            f.apply((v,))

        # Incorrect dim
        msg = """\
Invalid operation is performed in: FunctionNode \\(Forward\\)

Expect: in_types\\[0\\]\\.ndim >= 2
Actual: 1 < 2"""

        v = chainer.Variable(numpy.random.randn(5).astype(numpy.float32))
        with six.assertRaisesRegex(self, chainer.utils.type_check.InvalidType,
                                   msg):
            f.apply((v,))


@testing.parameterize(
    {'return_value': (numpy.array([float('nan')], numpy.float32),),
     'valid': False},
    {'return_value': (numpy.array([1], numpy.int32),), 'valid': True},
)
class TestFunctionNodeForwardDebug(unittest.TestCase):

    def setUp(self):
        self.original_debug = chainer.is_debug()
        chainer.set_debug(True)
        self.one = numpy.array([1], numpy.float32)
        self.f = chainer.FunctionNode()

    def tearDown(self):
        chainer.set_debug(self.original_debug)

    def check_debug_forward(self, x_data):
        x = chainer.Variable(x_data)
        if self.valid:
            # check if forward throws nothing
            self.f.apply((x,))
        else:
            with self.assertRaises(RuntimeError):
                self.f.apply((x,))

    def test_debug_forward_cpu(self):
        self.f.forward_cpu = mock.MagicMock(return_value=self.return_value)
        self.check_debug_forward(self.one)

    @attr.gpu
    def test_debug_forward_gpu(self):
        return_value = tuple(None if x is None else cuda.to_gpu(x)
                             for x in self.return_value)
        self.f.forward_gpu = mock.MagicMock(return_value=return_value)
        self.check_debug_forward(cuda.to_gpu(self.one))


@testing.parameterize(
    {'return_data': (numpy.array([float('nan')], numpy.float32),),
     'valid': False},
    {'return_data': (None,), 'valid': True},
)
class TestFunctionNodeBackwardDebug(unittest.TestCase):

    def setUp(self):
        self.original_debug = chainer.is_debug()
        chainer.set_debug(True)
        self.one = numpy.array([1], numpy.float32)
        self.f = chainer.FunctionNode()
        self.return_value = tuple(None if x is None else chainer.Variable(x)
                                  for x in self.return_data)

    def tearDown(self):
        chainer.set_debug(self.original_debug)

    def check_debug_backward_accumulate(self, *xs_data):
        xs = [chainer.Variable(x) for x in xs_data]
        y, = self.f.apply(xs)
        if self.valid:
            # check if backard throws nothing
            y.backward()
        else:
            with self.assertRaises(RuntimeError):
                y.backward()

    def test_debug_backward_accumulate_cpu(self):
        self.f.forward_cpu = mock.MagicMock(return_value=(self.one,))
        self.f.backward = mock.MagicMock(return_value=self.return_value)
        input_value = (self.one,) * len(self.return_value)
        self.check_debug_backward_accumulate(*input_value)

    @attr.gpu
    def test_debug_backward_accumulate_gpu(self):
        self.f.forward_gpu = mock.MagicMock(
            return_value=(cuda.to_gpu(self.one),))
        for x in self.return_value:
            if x is not None:
                x.to_gpu()
        input_value = (cuda.to_gpu(self.one),) * len(self.return_value)
        self.f.backward = mock.MagicMock(return_value=self.return_value)
        self.check_debug_backward_accumulate(*input_value)


class TestNoBackpropMode(unittest.TestCase):

    def setUp(self):
        self.x = chainer.Variable(numpy.array([1.], 'f'))

    def test_no_backprop_mode(self):
        y = self.x + 1
        self.assertTrue(y.creator_node is not None)

        with chainer.no_backprop_mode():
            y = self.x + 1
        self.assertTrue(y.creator_node is None)

        y = self.x + 1
        self.assertTrue(y.creator_node is not None)

    def test_force_backprop_mode(self):
        with chainer.no_backprop_mode():
            with chainer.force_backprop_mode():
                y = self.x + 1
        self.assertTrue(y.creator_node is not None)

        y = self.x + 1
        self.assertTrue(y.creator_node is not None)

        with chainer.force_backprop_mode():
            y = self.x + 1
        self.assertTrue(y.creator_node is not None)


class MyThread(threading.Thread):

    def run(self):
        x = chainer.Variable(numpy.array([1], dtype='f'))
        with chainer.no_backprop_mode():
            y = x + 1
        self.creator_is_none = y.creator_node is None


class TestBackpropModeMultiThread(unittest.TestCase):

    def test_multi_thread(self):
        t = MyThread()
        t.start()
        t.join()
        self.assertTrue(t.creator_is_none)


class FunctionNodeWithRetaining(chainer.FunctionNode):

    def forward(self, inputs):
        self.retain_inputs([1])
        self.retain_outputs([1])
        return inputs

    def backward(self, _, grad_outputs):
        self.backward_inputs = self.get_retained_inputs()
        self.backward_outputs = self.get_retained_outputs()
        return grad_outputs


class TestFunctionNodeRetaining(unittest.TestCase):

    def setUp(self):
        inputs = [chainer.Variable(numpy.array([1], dtype=numpy.float32)),
                  chainer.Variable(numpy.array([1], dtype=numpy.float32))]
        self.input_data = [x.data for x in inputs]
        self.input_nodes = [x.node for x in inputs]

        self.f1 = FunctionNodeWithRetaining()
        outputs = self.f1.apply(inputs)
        outputs[0].grad = numpy.array([1], dtype=numpy.float32)
        outputs[0].backward()
        self.f1_output_data = [y.data for y in outputs]
        self.f1_output_nodes = [y.node for y in outputs]

        inputs = None  # release non-retained inputs

    def test_retain_inputs(self):
        self.assertEqual(len(self.f1.backward_inputs), 1)
        self.assertIs(self.f1.backward_inputs[0].node, self.input_nodes[1])
        numpy.testing.assert_array_equal(self.f1.backward_inputs[0].data,
                                         self.input_data[1])

    def test_retain_outputs_f1(self):
        self.assertEqual(len(self.f1.backward_outputs), 1)
        numpy.testing.assert_array_equal(self.f1.backward_outputs[0].data,
                                         self.f1_output_data[1])


testing.run_module(__name__, __file__)
