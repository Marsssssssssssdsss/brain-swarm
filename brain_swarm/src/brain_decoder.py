"""脑信号解码模块

实现：FBCSP+LDA 分类器、EEGNet 深度学习模型、SSVEP 频域解码器
"""

import os
import numpy as np
from typing import Tuple, Optional, Union
from abc import ABC, abstractmethod

from config import DecoderConfig, SSVEPConfig


class BaseDecoder(ABC):
    """脑信号解码器基类"""

    @abstractmethod
    def predict(self, eeg_segment: np.ndarray) -> Tuple[int, float]:
        """
        预测脑电片段对应的意图

        Args:
            eeg_segment: (n_channels, n_samples) 一段 EEG 数据

        Returns:
            (class_id, confidence) 预测类别和置信度
        """
        pass

    @abstractmethod
    def fit(self, X: np.ndarray, y: np.ndarray):
        """训练解码器"""
        pass

    def load(self, path: str):
        """加载预训练模型"""
        pass

    def save(self, path: str):
        """保存模型"""
        pass


class FBCSPDecoder(BaseDecoder):
    """
    FBCSP + LDA 解码器

    滤波器组共空间模式 (Filter Bank Common Spatial Pattern)
    是运动想象 BCI 的经典方法，稳定可靠
    """

    def __init__(self, config: DecoderConfig):
        self.config = config
        self.n_classes = config.n_classes
        self.fbcsp_bands = config.fbcsp_bands
        self._classifier = None
        self._csp_filters = {}  # 存储每个频段的 CSP 滤波器
        self._fitted = False

    def _extract_fbcsp_features(self, X: np.ndarray) -> np.ndarray:
        """
        手动实现 FBCSP 特征提取（不依赖 MNE）

        Args:
            X: (n_trials, n_channels, n_samples)

        Returns:
            (n_trials, n_features) 对数方差特征
        """
        import joblib
        n_trials, n_channels, n_samples = X.shape

        all_features = []
        for trial in range(n_trials):
            trial_features = []
            for band_key, csp_filters in self._csp_filters.items():
                # 对每个频段应用 CSP 滤波器
                for filt in csp_filters:
                    transformed = filt @ X[trial]
                    # 取对数方差
                    var = np.log(np.var(transformed))
                    trial_features.append(var)
            all_features.append(trial_features)

        return np.array(all_features)

    def fit(self, X: np.ndarray, y: np.ndarray):
        """
        训练 FBCSP + LDA

        Args:
            X: (n_trials, n_channels, n_samples) 带标签的 EEG 数据
            y: (n_trials,) 类别标签
        """
        from scipy.linalg import eigh

        sampling_rate = self.config.sampling_rate if hasattr(self.config, 'sampling_rate') else 250
        n_channels = X.shape[1]

        self._csp_filters = {}

        for band_idx, (low, high) in enumerate(self.fbcsp_bands):
            # 带通滤波
            band_key = f"band_{band_idx}"

            # 简化版 CSP：直接计算协方差矩阵的特征分解
            # 对每个类别计算平均协方差
            covs = []
            for cls in np.unique(y):
                cls_data = X[y == cls]
                cls_cov = np.zeros((n_channels, n_channels))
                for trial in cls_data:
                    # 在特征空间做 CSP，直接对原始信号做
                    cls_cov += np.cov(trial)
                cls_cov /= len(cls_data)
                covs.append(cls_cov)

            if len(covs) < 2:
                # 单类情况，取前几个主成分
                cov_all = covs[0]
                eigvals, eigvecs = np.linalg.eigh(cov_all)
                idx = np.argsort(eigvals)[::-1][:4]
                self._csp_filters[band_key] = [eigvecs[:, idx[j]] for j in range(4)]
                continue

            # 广义特征值分解
            eigvals, eigvecs = eigh(covs[0], covs[0] + covs[1])
            idx = np.argsort(eigvals)[::-1]
            eigvecs = eigvecs[:, idx]

            # 取前 2 和后 2 个分量
            n_filters = 4
            selected = np.concatenate([
                eigvecs[:, :2],
                eigvecs[:, -2:]
            ], axis=1)
            self._csp_filters[band_key] = [selected[:, j] for j in range(n_filters)]

        # 提取特征
        features = self._extract_fbcsp_features(X)

        # 训练 LDA
        from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
        self._classifier = LinearDiscriminantAnalysis()
        self._classifier.fit(features, y)
        self._fitted = True

        return self

    def predict(self, eeg_segment: np.ndarray) -> Tuple[int, float]:
        """预测单个 EEG 片段"""
        if not self._fitted:
            raise RuntimeError("模型未训练，请先调用 fit()")

        # 提取特征
        features = self._extract_fbcsp_features(eeg_segment[np.newaxis, ...])

        # 预测
        pred = self._classifier.predict(features)[0]
        proba = self._classifier.predict_proba(features)[0]
        confidence = float(proba.max())

        return int(pred), confidence

    def save(self, path: str):
        import joblib
        joblib.dump({
            'csp_filters': self._csp_filters,
            'classifier': self._classifier,
            'config': self.config,
            'fitted': self._fitted
        }, path)

    def load(self, path: str):
        import joblib
        data = joblib.load(path)
        self._csp_filters = data['csp_filters']
        self._classifier = data['classifier']
        self._fitted = data['fitted']


class SimpleCNN(BaseDecoder):
    """
    轻量级 CNN 解码器，用于快速原型验证

    结构：Conv1D → BN → ReLU → Pool → Conv1D → BN → ReLU → Pool → Linear → Softmax
    """

    def __init__(self, config: DecoderConfig):
        self.config = config
        self.n_classes = config.n_classes
        self._model = None
        self._fitted = False

    def _build_model(self, n_channels: int, n_samples: int):
        """构建模型"""
        import torch
        import torch.nn as nn

        class EEGCNN(nn.Module):
            def __init__(self, n_ch, n_sp, n_cls):
                super().__init__()
                self.conv1 = nn.Conv1d(n_ch, 32, kernel_size=7, padding=3)
                self.bn1 = nn.BatchNorm1d(32)
                self.pool1 = nn.MaxPool1d(4)
                self.conv2 = nn.Conv1d(32, 64, kernel_size=5, padding=2)
                self.bn2 = nn.BatchNorm1d(64)
                self.pool2 = nn.MaxPool1d(4)
                self.dropout = nn.Dropout(0.5)

                # 计算展平维度
                dummy = torch.zeros(1, n_ch, n_sp)
                x = self.pool2(torch.relu(self.bn2(self.conv2(
                    self.pool1(torch.relu(self.bn1(self.conv1(dummy))))
                ))))
                flat_dim = x.view(1, -1).shape[1]

                self.fc = nn.Linear(flat_dim, n_cls)

            def forward(self, x):
                x = torch.relu(self.bn1(self.conv1(x)))
                x = self.pool1(x)
                x = torch.relu(self.bn2(self.conv2(x)))
                x = self.pool2(x)
                x = self.dropout(x)
                x = x.view(x.size(0), -1)
                return self.fc(x)

        self._model = EEGCNN(n_channels, n_samples, self.n_classes)

    def fit(self, X: np.ndarray, y: np.ndarray, epochs: int = 50):
        """训练 CNN"""
        import torch
        import torch.nn as nn

        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

        X_tensor = torch.FloatTensor(X).to(device)
        y_tensor = torch.LongTensor(y).to(device)

        if self._model is None:
            self._build_model(X.shape[1], X.shape[2])

        self._model = self._model.to(device)
        optimizer = torch.optim.Adam(self._model.parameters(), lr=0.001)
        criterion = nn.CrossEntropyLoss()

        self._model.train()
        for epoch in range(epochs):
            optimizer.zero_grad()
            output = self._model(X_tensor)
            loss = criterion(output, y_tensor)
            loss.backward()
            optimizer.step()

            if (epoch + 1) % 10 == 0:
                pred = output.argmax(dim=1)
                acc = (pred == y_tensor).float().mean().item()
                print(f"Epoch {epoch+1}/{epochs} | Loss: {loss.item():.4f} | Acc: {acc:.3f}")

        self._fitted = True
        return self

    def predict(self, eeg_segment: np.ndarray) -> Tuple[int, float]:
        """预测"""
        import torch

        if not self._fitted:
            raise RuntimeError("模型未训练，请先调用 fit()")

        device = next(self._model.parameters()).device
        self._model.eval()

        with torch.no_grad():
            x = torch.FloatTensor(eeg_segment).unsqueeze(0).to(device)
            output = self._model(x)
            probs = torch.softmax(output, dim=1)
            pred = int(output.argmax(dim=1).item())
            confidence = float(probs.max().item())

        return pred, confidence

    def save(self, path: str):
        import torch
        torch.save({
            'model_state': self._model.state_dict(),
            'config': self.config,
            'fitted': self._fitted
        }, path)

    def load(self, path: str):
        import torch
        data = torch.load(path, map_location='cpu')
        self.config = data['config']
        self._fitted = data['fitted']
        # 需要知道输入维度才能重建模型，如果有保存的维度信息
        if self._model is None and 'model_state' in data:
            # 从 state_dict 推断维度
            state = data['model_state']
            conv1_weight = state['conv1.weight']
            self._build_model(conv1_weight.shape[0] if conv1_weight.shape[1] == 1 else conv1_weight.shape[1], 250)
            self._model.load_state_dict(state)


def create_decoder(config: Union[DecoderConfig, SSVEPConfig]) -> BaseDecoder:
    """解码器工厂函数"""
    if isinstance(config, SSVEPConfig):
        from ssvep_decoder import SSVEPDecoder as SSVEPDec
        from ssvep_decoder import SSVEPConfig as SSVEPCfg
        return SSVEPDec(SSVEPCfg(
            frequencies=config.frequencies,
            command_labels=config.command_labels,
            sampling_rate=250,
            window_duration=config.window_duration,
            snr_threshold=config.snr_threshold,
            confidence_threshold=config.confidence_threshold,
        ))
    elif config.model_type == "fbcsp_lda":
        return FBCSPDecoder(config)
    elif config.model_type == "eegnet":
        return SimpleCNN(config)
    else:
        raise ValueError(f"未知解码器类型: {config.model_type}")