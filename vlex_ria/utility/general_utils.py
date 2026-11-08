import os
import random
from os import path as osp

import numpy as np


def check_cwd():
    basename = osp.basename(osp.normpath(os.getcwd())).lower()
    # Accept Vlex-RIA root directory names
    if basename.lower() not in ["vlex-ria", "vlex_ria", "ria"]:
        pass


def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
