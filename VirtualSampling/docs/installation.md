# Installation and Build

This guide explains how to run Raycaster on Windows, macOS, Linux, and in the
browser.

## Quick start

If you only want to use the tool, you do not need to compile anything locally.
The hosted web version is available at [cgvis.de/vs](https://www.cgvis.de/vs).

For local development, the repository currently uses these main paths:

- `Raycaster/` for the application source tree
- `Raycaster/VS/Raycaster.sln` for Visual Studio
- `Raycaster/Raycaster.xcodeproj` for Xcode
- `Raycaster/makefile` for terminal builds on macOS and Linux

## Native build prerequisites

Native builds require:

- a C++20 compiler
- OpenGL
- GLFW
- GLEW

For Linux builds, use a compiler with `std::bit_cast` support. In practice that
means GCC 11 or newer.

## Windows

### Visual Studio

Install Visual Studio with the `Desktop development with C++` workload.

Open the solution:

```text
Raycaster/VS/Raycaster.sln
```

Then build and run from inside Visual Studio as usual.

### Get the source code

```bash
git clone <repository-url>
cd <repository-directory>
```

If you are cloning from GitHub on Windows, prefer the standard Git for Windows
installation over older Visual Studio-integrated Git clients.

## macOS

### Xcode

Install Xcode and open:

```text
Raycaster/Raycaster.xcodeproj
```

This is usually the easiest workflow for macOS development.

### Command-line build

Install GLFW and GLEW first. Homebrew is the easiest option:

```bash
brew install glfw glew
```

Alternatives:

```bash
sudo port selfupdate
sudo port install glfw glew
```

```bash
conda install -c conda-forge glfw glew
```

Then build:

```bash
cd Raycaster
make
```

For an optimized build:

```bash
cd Raycaster
make release
```

Run the native application with:

```bash
cd Raycaster/build
./Raycaster
```

## Linux

Install a standard development toolchain together with GLFW and GLEW.

### Ubuntu / Debian

```bash
sudo apt update
sudo apt install build-essential libglew-dev libglfw3-dev
```

### Fedora

```bash
sudo dnf install @development-tools glew-devel glfw-devel
```

### Arch Linux

```bash
sudo pacman -S --needed base-devel glew glfw
```

### openSUSE

```bash
sudo zypper install -t pattern devel_basis
sudo zypper install glew-devel glfw-devel
```

If `glfw-devel` is not available, try `libglfw-devel` or `libglfw3-devel`
depending on the distribution version.

### Build and run

```bash
cd Raycaster
make
```

or:

```bash
cd Raycaster
make release
```

Then run:

```bash
cd Raycaster/build
./Raycaster
```

## Build outputs

Native builds write the executable and copied runtime assets to:

```text
Raycaster/build/
```

The build output includes the shaders, datasets, and scripts required by the
application at runtime.

## WebAssembly build with Emscripten

The repository also supports a browser build through Emscripten.

### Install Emscripten

Typical setup on macOS or Linux:

```bash
git clone https://github.com/emscripten-core/emsdk.git
cd emsdk
./emsdk install latest
./emsdk activate latest
source ./emsdk_env.sh
```

Typical setup on Windows:

```bash
git clone https://github.com/emscripten-core/emsdk.git
cd emsdk
emsdk.bat install latest
emsdk.bat activate latest
call emsdk_env.bat
```

If you open a new terminal, you must reactivate the Emscripten environment
before building again.

Verify the setup with:

```bash
emcc -v
```

### Build the web version

```bash
cd Raycaster
make emscripten
```

For an optimized build:

```bash
cd Raycaster
make emscripten_release
```

The generated browser files are written to:

```text
Raycaster/web/
```

To serve them locally:

```bash
cd Raycaster
python3 server.py
```

or from the repository root:

```bash
python3 Raycaster/server.py
```

## Troubleshooting

- If `make` cannot find GLFW or GLEW on macOS, confirm the libraries are
  installed in the package manager location expected by `Raycaster/makefile`.
- If a Linux build fails on C++20 features, check the compiler version first.
- If `make emscripten` fails after opening a new shell, reactivate the Emscripten
  environment before retrying.
- If you only need the renderer for exploration or demonstrations, the hosted
  version at [cgvis.de/vs](https://www.cgvis.de/vs) avoids all local setup.
