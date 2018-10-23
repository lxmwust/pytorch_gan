import torch
import math
from .layers.categorical_batch_norm import CategoricalBatchNorm
from.layers.spectral_norm import SpectralNorm


class Block(torch.nn.Module):

    def __init__(self, in_channels, out_channels, hidden_channels=None,
                 kernel_size=3, stride=1, padding=1, optimized=False):
        super(Block, self).__init__()
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.optimized = optimized
        self.hidden_channels = out_channels if not hidden_channels else hidden_channels

        self.conv1 = torch.nn.Conv2d(self.in_channels, self.hidden_channels,
                                     kernel_size=kernel_size, stride=stride, padding=padding)
        self.conv2 = torch.nn.Conv2d(self.hidden_channels, self.out_channels,
                                     kernel_size=kernel_size, stride=stride, padding=padding)
        self.s_conv = None
        torch.nn.init.xavier_uniform_(self.conv1.weight.data, math.sqrt(2))
        torch.nn.init.xavier_uniform_(self.conv2.weight.data, math.sqrt(2))
        if self.in_channels != self.out_channels or optimized:
            self.s_conv = torch.nn.Conv2d(self.in_channels, self.out_channels, kernel_size=1, padding=0)
            torch.nn.init.xavier_uniform_(self.s_conv.weight.data, 1.)
        self.activate = torch.nn.ReLU()

    def forward(self, input):
        x_r = input
        x = self.conv1(input)
        x = self.activate(x)
        x = self.conv2(x)
        if self.optimized:
            x = torch.nn.functional.avg_pool2d(x, 2)
            x_r = torch.nn.functional.avg_pool2d(x_r, 2)
        if self.s_conv:
            x_r = self.s_conv(x_r)
        return x + x_r



class Gblock(Block):

    def __init__(self, in_channels, out_channels, hidden_channels=None, num_categories=None,
                 kernel_size=3, stride=1, padding=1, upsample=True):
        super(Gblock, self).__init__(in_channels, out_channels, hidden_channels, kernel_size, stride, padding)
        self.upsample = upsample
        self.num_categories = num_categories

        self.bn1 = self.batch_norm(self.in_channels)
        self.bn2 = self.batch_norm(self.hidden_channels)
        self.up = lambda a: torch.nn.functional.interpolate(a, scale_factor=2)

    def batch_norm(self, num_features):
        return torch.nn.BatchNorm2d(num_features) if not self.num_categories \
            else CategoricalBatchNorm(num_features, self.num_categories)

    def forward(self, input, y=None):
        x = input
        x_r = input
        x = self.bn1(x, y) if self.num_categories else self.bn1(x)
        x = self.activate(x)
        if self.upsample:
            x = self.up(x)
            x_r = self.up(x_r)
        x = self.conv1(x)
        x = self.bn2(x, y) if self.num_categories else self.bn2(x)
        x = self.activate(x)
        x = self.conv2(x)
        if self.s_conv:
            x_r = self.s_conv(x_r)
        return x + x_r

class Dblock(Block):

    def __init__(self, in_channels, out_channels, hidden_channels=None, kernel_size=3, stride=1, padding=1,
                 downsample=False):
        super(Dblock, self).__init__(in_channels, out_channels, hidden_channels, kernel_size, stride, padding)
        self.downsample = downsample
        self.conv1 = SpectralNorm(self.conv1)
        self.conv2 = SpectralNorm(self.conv2)
        if self.s_conv:
            self.s_conv = SpectralNorm(self.s_conv)

    def forward(self, input):
        x_r = input
        if self.s_conv:
            x_r = self.s_conv(x_r)
        x = self.activate(input)
        x = self.conv1(x)
        x = self.activate(x)
        x = self.conv2(x)
        if self.downsample:
            x = torch.nn.functional.avg_pool2d(x, 2)
            x_r = torch.nn.functional.avg_pool2d(x_r, 2)
        return x + x_r


class ResnetGenerator(torch.nn.Module):

    def __init__(self, ch, z_dim, n_categories=None, bottom_width=4):
        super(ResnetGenerator, self).__init__()
        self.z_dim = z_dim
        self.ch = ch
        self.n_categories = n_categories
        self.bottom_width = bottom_width
        self.blocks = []
        self.block_op = torch.nn.ModuleList()
        self.final = self.final_block()

    def final_block(self):
        conv = torch.nn.Conv2d(self.ch, 3, kernel_size=3, stride=1, padding=1)
        torch.nn.init.xavier_uniform_(conv.weight.data, 1.)
        final_ = torch.nn.Sequential(
            torch.nn.BatchNorm2d(self.ch),
            torch.nn.ReLU(),
            conv,
            torch.nn.Tanh()
        )
        return final_


    def forward(self, input, y=None):
        x = self.dense(input)
        x = x.view(x.shape[0], -1, self.bottom_width, self.bottom_width)
        for block in self.block_op:
            x = block(x, y)
        x = self.final(x)
        return x


class ResnetGenerator128(ResnetGenerator):

    def __init__(self, ch=64, z_dim=128, n_categories=None, bottom_width=4):
        super(ResnetGenerator128, self).__init__(ch, z_dim, n_categories, bottom_width)
        self.dense = torch.nn.Linear(self.z_dim, self.bottom_width * self.bottom_width * self.ch * 16)
        torch.nn.init.xavier_uniform_(self.dense.weight.data, 1.)
        self.blocks.append(Gblock(self.ch*16, self.ch*16, upsample=True, num_categories=self.n_categories))
        self.blocks.append(Gblock(self.ch * 16, self.ch * 8, upsample=True, num_categories=self.n_categories))
        self.blocks.append(Gblock(self.ch * 8, self.ch * 4, upsample=True, num_categories=self.n_categories))
        self.blocks.append(Gblock(self.ch * 4, self.ch * 2, upsample=True, num_categories=self.n_categories))
        self.blocks.append(Gblock(self.ch * 2, self.ch, upsample=True, num_categories=self.n_categories))
        self.block_op = torch.nn.Sequential(*self.blocks)
        self.final = self.final_block()


class ResnetGenerator32(ResnetGenerator):

    def __init__(self, ch=256, z_dim=128, n_categories=None, bottom_width=4):
        super(ResnetGenerator32, self).__init__(ch, z_dim, n_categories, bottom_width)
        self.dense = torch.nn.Linear(self.z_dim, self.bottom_width * self.bottom_width * self.ch)
        torch.nn.init.xavier_uniform_(self.dense.weight.data, 1.)
        self.blocks.append(Gblock(self.ch, self.ch, upsample=True, num_categories=self.n_categories))
        self.blocks.append(Gblock(self.ch, self.ch, upsample=True, num_categories=self.n_categories))
        self.blocks.append(Gblock(self.ch, self.ch, upsample=True, num_categories=self.n_categories))
        self.block_op = torch.nn.Sequential(*self.blocks)



class ResnetDiscriminator(torch.nn.Module):

    def __init__(self, ch, n_categories=0):
        super(ResnetDiscriminator, self).__init__()
        self.activate = torch.nn.ReLU()
        self.ch = ch
        self.n_categories = n_categories
        self.blocks = [Block(3, self.ch, optimized=True)]
        self.block_op = torch.nn.Sequential()

    def forward(self, input, y=None):
        x = self.block_op(input)
        x = self.activate(x)
        x = torch.sum(x, (2, 3))
        output = self.l(x)
        if y is not None:
            w_y = self.l_y(y)
            output += torch.sum(w_y*x, dim=1, keepdim=True)
        return output


class ResnetDiscriminator128(ResnetDiscriminator):

    def __init__(self, ch=64, n_categories=0):
        super(ResnetDiscriminator128, self).__init__(ch, n_categories)

        self.blocks.append(Dblock(self.ch, self.ch*2, downsample=True))
        self.blocks.append(Dblock(self.ch*2, self.ch*4, downsample=True))
        self.blocks.append(Dblock(self.ch*4, self.ch*8, downsample=True))
        self.blocks.append(Dblock(self.ch*8, self.ch*16, downsample=True))
        self.blocks.append(Dblock(self.ch*16, self.ch*16, downsample=True))
        self.block_op = torch.nn.Sequential(*self.blocks)
        self.l = SpectralNorm(torch.nn.Linear(self.ch*16, 1))
        torch.nn.init.xavier_uniform_(self.l.module.weight.data, 1.)
        if n_categories > 0:
            self.l_y = SpectralNorm(torch.nn.Embedding(n_categories, self.ch*16))
            torch.nn.init.xavier_uniform_(self.l_y.module.weight.data, 1.)


class ResnetDiscriminator32(ResnetDiscriminator):

    def __init__(self, ch=128, n_categories=0):
        super(ResnetDiscriminator32, self).__init__(ch, n_categories)
        self.blocks += [
            Dblock(self.ch, self.ch, downsample=True),
            Dblock(self.ch, self.ch, downsample=True),
            Dblock(self.ch, self.ch, downsample=True)
            ]
        self.block_op = torch.nn.Sequential(*self.blocks)

        self.l = SpectralNorm(torch.nn.Linear(self.ch, 1, bias=True))
        if n_categories > 0:
            self.l_y = SpectralNorm(torch.nn.Embedding(n_categories, self.ch))

