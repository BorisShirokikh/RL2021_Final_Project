import os
from pathlib import Path
import argparse

from torch.utils.tensorboard import SummaryWriter

from dpipe.io import choose_existing
from dpipe.torch import save_model_state, load_model_state

from ttt_lib.torch.module.policy_net import PolicyNetworkQ10Light, PolicyNetworkQ10
from ttt_lib.policy_player import PolicyPlayer
from ttt_lib.q_learning import train_q_learning
from ttt_lib.off_policy_tree_backup import train_tree_backup
from ttt_lib.field import Field


if __name__ == '__main__':

    Q_EXP_PATH = choose_existing(
        Path('/nmnt/x4-hdd/experiments/rl/q_10x10/'),
    )

    parser = argparse.ArgumentParser()

    parser.add_argument('--exp_name', required=True, type=str)
    parser.add_argument('--method', required=True, type=str, choices=('q_learning', 'tree_backup'))
    parser.add_argument('--n_step_q', required=True, type=int)
    parser.add_argument('--lam', required=False, type=float, default=None)

    parser.add_argument('--lr', required=False, type=float, default=1e-5)

    parser.add_argument('--duel_name', required=False, type=str, default=None)
    parser.add_argument('--preload_path', required=False, type=str, default=None)

    parser.add_argument('--large_model', required=False, action='store_true', default=False)

    args = parser.parse_known_args()[0]

    exp_path = Q_EXP_PATH / args.exp_name
    os.makedirs(exp_path, exist_ok=True)

    duel_path = None if args.duel_name is None else Q_EXP_PATH / args.duel_name

    log_dir = exp_path / 'logs'
    log_dir.mkdir(exist_ok=True)

    logger = SummaryWriter(log_dir=log_dir)

    n_step_q = args.n_step_q
    lam = args.lam

    # hyperparameters:
    n = 10
    kernel_len = 5
    lr = args.lr
    device = 'cuda'

    field = Field(n=n, kernel_len=kernel_len, device=device, check_device=device)

    cnn_features = (128, 64)
    if args.large_model:
        model = PolicyNetworkQ10(n=n, structure=cnn_features)
    else:
        model = PolicyNetworkQ10Light(n=n, structure=cnn_features)

    preload_path = args.preload_path
    if preload_path is not None:
        load_model_state(model, preload_path)

    n_episodes = 2000000
    eps_init = 0.5
    ep2eps = {int(7e5): 0.4, int(10e5): 0.3, int(12e5): 0.3, int(14e5): 0.2, int(16e5): 0.1, }

    player = PolicyPlayer(model=model, field=field, eps=eps_init, device=device)

    # TRAIN:
    method = args.method
    if method == 'q_learning':
        train_q_learning(player, logger, exp_path, n_episodes, augm=True, n_step_q=n_step_q, lam=lam,
                         ep2eps=ep2eps, lr=lr,
                         episodes_per_epoch=10000, n_duels=100, episodes_per_model_save=10000,
                         duel_path=duel_path)
    elif method == 'tree_backup':
        train_tree_backup(player, logger, exp_path, n_episodes, augm=True, n_step_q=n_step_q, lam=lam,
                          ep2eps=ep2eps, lr=lr,
                          episodes_per_epoch=10000, n_duels=100, episodes_per_model_save=10000,
                          duel_path=duel_path)
    # SAVE:
    save_model_state(player.model, exp_path / 'model.pth')
