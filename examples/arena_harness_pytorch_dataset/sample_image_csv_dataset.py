"""PyTorch Dataset: images from a local folder paired with a CSV manifest."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import torch
from PIL import Image
from torch.utils.data import Dataset


class ImageCsvDataset(Dataset):
    """Load RGB images from `image_dir` using rows from a paired CSV file.

    Expected CSV columns:
      - filename: image file name relative to image_dir
      - label: integer class id
    """

    def __init__(self, image_dir: str, csv_path: str, transform=None) -> None:
        self.image_dir = Path(image_dir)
        self.csv_path = Path(csv_path)
        self.transform = transform

        if not self.image_dir.is_dir():
            raise FileNotFoundError(f"Image directory not found: {self.image_dir}")
        if not self.csv_path.is_file():
            raise FileNotFoundError(f"CSV manifest not found: {self.csv_path}")

        self.manifest = pd.read_csv(self.csv_path)
        required = {"filename", "label"}
        missing = required - set(self.manifest.columns)
        if missing:
            raise ValueError(f"CSV missing required columns: {sorted(missing)}")

    def __len__(self) -> int:
        return len(self.manifest)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, int]:
        row = self.manifest.iloc[idx]
        image_path = self.image_dir / str(row["filename"])
        if not image_path.is_file():
            raise FileNotFoundError(f"Missing image file: {image_path}")

        image = Image.open(image_path).convert("RGB")
        label = int(row["label"])

        if self.transform is not None:
            image = self.transform(image)

        if not isinstance(image, torch.Tensor):
            image = torch.as_tensor(image)

        return image, label
