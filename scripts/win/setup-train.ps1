# Create training venv with PyTorch CUDA 12.4 (matches RTX 3070 Laptop).
# Run from repo root: .\scripts\win\setup-train.ps1
$ErrorActionPreference = "Stop"

if (-not (Test-Path "venv-train")) {
    python -m venv venv-train
}

& venv-train\Scripts\Activate.ps1

python -m pip install --upgrade pip

# PyTorch with CUDA 12.4 (RTX 3070 Laptop has 8 GB VRAM)
pip install torch torchvision `
    --index-url https://download.pytorch.org/whl/cu124

# Other training deps
pip install -r ml/requirements-train.txt

# Verify CUDA
python -c "import torch; assert torch.cuda.is_available(), 'CUDA not available'; print('CUDA OK:', torch.cuda.get_device_name(0))"

Write-Host ""
Write-Host "Training venv ready. Activate with: venv-train\Scripts\Activate.ps1"
