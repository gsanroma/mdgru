__author__ = "Simon Andermatt"
__copyright__ = "Copyright (C) 2017 Simon Andermatt"

from copy import copy, deepcopy

import torch as th

from helper import compile_arguments, harmonize_filter_size
from ..crnn.cgru import CGRUCell


class MDRNN(th.nn.Module):
    """MDRNN class originally designed to handle the sum of cGRU computations resulting in one MDGRU.

    _defaults contains initial values for most class attributes.
    :param use_dropconnect_x: Flag if dropconnect regularization should be applied to input weights
    :param use_dropconnect_h: Flag if dropconnect regularization should be applied to state weights
    :param return_cgru_results: Flag if instead of a sum, the individual cgru results should be returned
    :param filter_size_x: Dimensions of filters for the input (the current time dimension is ignored in each cRNN)
    :param filter_size_h: Dimensions of filters for the state (the current time dimension is ignored in each cRNN)
    :param crnn_activation: Activation function for the candidate / state / output in each cRNN
    :param legacy_cgru_addition: Activating old implementation of crnn sum, for backwards compatibility
    :param crnn_class: Which cRNN class should be used (CGRUCell for MDGRU)
    :param strides: Defines strides to be applied along each dimension

    :param inputarr: Input data, needs to be in shape [batch, spatialdim1...spatialdimn, channel]
    :param dropout: Dropoutrate to be applied
    :param dimensions: which dimensions should be processed with a cRNN (by default all of them)
    :param num_hidden: How many hidden units / channels does this MDRNN have
    :param name: What should be the name of this MDRNN

    """
    _defaults = {
        "use_dropconnect_x": {'value': True, 'help': "Should Dropconnect be applied to the input?", 'invert_meaning': 'dont_'},
        "use_dropconnect_h": {'value': True, 'help': "Should DropConnect be applied to the state?", 'invert_meaning': 'dont_'},
        # "swap_memory": True,
        "return_cgru_results": {'value': False, 'help': "Instead of summing, individual cgru channel results are concatenated."},
        "filter_size_x": {'value': [7, 7, 7], 'help': "Convolution kernel size for input."},
        "filter_size_h": {'value': [7, 7, 7], 'help': "Convolution kernel size for state."},
        "crnn_activation": {'value': th.nn.Tanh, 'help': "Activation function to be used for the CRNN."},
        "legacy_cgru_addition": {'value': False, 'help': "results in worse weight initialization, only use if you know what you are doing!"},
        "crnn_class": {'value': CGRUCell, 'help': 'CRNN class to be used in the MDRNN'}, #this is silly as we wont be able to ever display the correct help message if this is changed....
        "strides": None,
        "name": "mdgru",
        "num_hidden": 100,
        "num_input": 6,
    }

    def __init__(self, dropout, spatial_dimensions, kw):
        super(MDRNN, self).__init__()
        mdgru_kw, kw = compile_arguments(MDRNN, kw, transitive=False)
        for k, v in mdgru_kw.items():
            setattr(self, k, v)
        self.filter_size_x = harmonize_filter_size(self.filter_size_x, len(spatial_dimensions))
        self.filter_size_h = harmonize_filter_size(self.filter_size_h, len(spatial_dimensions))
        self.crnn_kw, kw = compile_arguments(self.crnn_class, kw, transitive=True)
        self.spatial_dimensions = spatial_dimensions
        self.dropout = dropout
        cgrus = []
        if self.use_dropconnect_h:
            self.crnn_kw["dropconnecth"] = self.dropout
        else:
            self.crnn_kw["dropconnecth"] = None
        if self.use_dropconnect_x:
            self.crnn_kw["dropconnectx"] = self.dropout
        else:
            self.crnn_kw["dropconnectx"] = None

        for d in self.spatial_dimensions:
            fsx = deepcopy(self.filter_size_x)
            fsh = deepcopy(self.filter_size_h)
            fsx.pop(d)
            fsh.pop(d)
            if self.strides is not None:
                raise Exception('we do not allow strides yet in the pytorch version')
            else:
                st = None

            crnn_dim_options = copy(self.crnn_kw)

            crnn_dim_options["filter_size_x"] = fsx
            crnn_dim_options["filter_size_h"] = fsh
            crnn_dim_options["strides"] = copy(st)

            # forward and back direction
            bicgru = th.nn.ModuleList([self.crnn_class(self.num_input, self.num_hidden, copy(crnn_dim_options)),
                                         self.crnn_class(self.num_input, self.num_hidden, copy(crnn_dim_options))])
            cgrus.append(bicgru)
        self.cgrus = th.nn.ModuleList(cgrus)

    def forward(self, input):
        outputs = []
        for i, (d, cgrus) in enumerate(zip(self.spatial_dimensions, self.cgrus)):
            # split tensor along d:
            cgru_split_input = th.unbind(input, d + 2) #spatial dim, hence d + 2
            output = th.stack(cgrus[0].forward(cgru_split_input), d + 2) \
                     + th.stack(cgrus[1].forward(cgru_split_input[::-1])[::-1], d + 2)
            outputs.append(output)
        # transform the sum to a mean over all cgrus (self.cgrus contains birnn)
        return th.sum(th.stack(outputs, dim=0), dim=0) / (len(self.cgrus) * 2)