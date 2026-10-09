# ai_toolchain


AI Model Toolchain (remand description)

A model warehouse and soft link tool chain for unified management of Stable Diffusion WebUI (A1111) and ComfyUI multi-version.

The goal is to allow multiple applications and multiple versions to share the same set of model resources, so as to achieve higher storage utilization, a more stable production environment, and a more convenient development and testing process.

---

🎯 Project Objectives

- Model Warehouse

Store all model files in a standardized warehouse directory to avoid repeated disk space.

- Multi-application common model (A1111 / ComfyUI)

Automatically create soft links for different applications, so that they point to the same repository and realize model sharing.

- Multi-version shared model (ComfyUI multi-version)

Support ComfyUI multiple versions (production / development / testing) to use the same warehouse at the same time to avoid duplicate downloads and maintenance.

- Automated configuration and verification

Automatically create soft links, check the directory structure, and prompt missing or unmapped model directories.

- Extensible Python tool chain

In the future, it can be expanded to:

- workflow compatibility check

- automatic update of custom nodes

- Multi-version switching and marking

- Warehouse integrity scanning

- Repeated detection of the model

---

📦 Current function (minimum closed loop)

1. Unified model warehouse management

- Automatically read the warehouse path

- Automatically identify the standardized model directory in the warehouse

- Map the model directory of A1111 / ComfyUI to the warehouse directory

2. A1111 Model Soft Link Creation

- Automatically traverse the model directory of A1111

- Create `WLink → Warehouse Directory` according to the mapping relationship

- Automatically skip unmapped or non-existent directories

- Automatically delete the empty directory and then create a soft link

3. ComfyUI multi-version model soft link creation

- Support multiple ComfyUI versions

- Each version automatically traverses its `models/` subdirectory

- Create `WLink → Warehouse Directory` according to the mapping relationship

- Automatically skip unmapped or non-existent directories

4. Unified CLI operation

Provide a simple and consistent command line entrance:

Ai-toolchain a1111

Ai-toolchain comfyui

---

🧩 Configuration method

1. `paths.yaml`

Define the warehouse path, A1111 path, and ComfyUI multi-version path.

2. `mapping.yaml`

Define the model directory mapping relationship, such as:

- `Stable-diffusion → Checkpoints`

- `loras → Lora`

- `vae → VAE`

Users can adjust freely.

---

🚀 Usage scenario

- Production environment

The stable version of ComfyUI uses the same warehouse as A1111 to reduce maintenance costs.

- Development environment

The new version of ComfyUI can quickly access the same warehouse, and there is no need to download the model repeatedly to test new features.

- Test environment

When multiple versions coexist, the model directory is consistent, which is convenient for workflow migration and compatibility verification.

---

🔧 Technology stack

- Python 3.x

- Typer CLI

- Pathlib file system operation

- YAML configuration loading

---

📌 Design principles

- Separation of configuration and logic

All paths and mappings are defined in YAML.

- Modularity and scalability

The current implementation only contains the minimum closed loop shared by the model, and more functions can be expanded in the future.

- Cross-platform compatibility

Soft link logic is compatible with Windows and Linux.

---