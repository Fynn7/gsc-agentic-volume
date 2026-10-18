#include "Raycaster.h"
#include "CommandInterpreter.h"

#ifndef __EMSCRIPTEN__

#ifdef _WIN32
#include <Windows.h>

INT WinMain(HINSTANCE hInstance, HINSTANCE hPrevInstance, PSTR lpCmdLine, INT nCmdShow) {
  std::vector<std::string> args = getArgsWindows();
#else
  int main(int argc, char** argv) {
    std::vector<std::string> args{argv + 1, argv + argc};
#endif
    try {
      Raycaster raycaster(args);
      raycaster.run();
    }
    catch (const GLException& e) {
      std::stringstream ss;
      ss << "Insufficient OpenGL Support " << e.what();
#ifndef _WIN32
      std::cerr << ss.str().c_str() << std::endl;
#else
      MessageBoxA(
                  NULL,
                  ss.str().c_str(),
                  "OpenGL Error",
                  MB_ICONERROR | MB_OK
                  );
#endif
      return EXIT_FAILURE;
    }
    return EXIT_SUCCESS;
  }

#else

#include <emscripten/emscripten.h>
#include <cmath>

std::shared_ptr<Raycaster> raycaster = nullptr;

  extern "C" {

    EMSCRIPTEN_KEEPALIVE void setTFParams(int gaussian, float start, float width) {
      if (raycaster) {
        raycaster->setTFParams(gaussian != 0, start, width);
      }
    }

    EMSCRIPTEN_KEEPALIVE void setAlphaThreshold(float a) {
      if (raycaster) {
        raycaster->setAlphaThreshold(a);
      }
    }

    EMSCRIPTEN_KEEPALIVE void setCustomTF(const char* tfString) {
      if (raycaster) {
        raycaster->setCustomTF(tfString);
      }
    }

    EMSCRIPTEN_KEEPALIVE void setTransformParams(const char* transString) {
      if (raycaster) {
        raycaster->setTransformParams(transString);
      }
    }

    EMSCRIPTEN_KEEPALIVE void setDataset(int index) {
      if (raycaster) {
        raycaster->setVolume(index);
      }
    }

    EMSCRIPTEN_KEEPALIVE void setMethod(int index) {
      if (raycaster) {
        if (raycaster->getRendermode() != uint32_t(index))
          raycaster->setRendermode(index);
      }
    }

    EMSCRIPTEN_KEEPALIVE void setLevel(int level) {
      if (raycaster) {
        raycaster->setLevel(level);
      }
    }

    EMSCRIPTEN_KEEPALIVE void setConstantSampleCount(bool enabled) {
      if (raycaster) {
        raycaster->setConstantSampleCount(enabled);
      }
    }

    EMSCRIPTEN_KEEPALIVE void setUseOrtho(bool useOrtho) {
      if (raycaster) {
        raycaster->setUseOrtho(useOrtho);
      }
    }

    EMSCRIPTEN_KEEPALIVE void setVirtualSamples(int count) {
      if (raycaster) {
        raycaster->setVirtualSamples(std::max<int>(1,count));
      }
    }

    EMSCRIPTEN_KEEPALIVE void setSamplingRateExp(int exp2) {
      if (raycaster) {
        raycaster->setSampling(std::ldexp(1.0, exp2));
      }
    }

    EMSCRIPTEN_KEEPALIVE void setBackground(float r, float g, float b, float a) {
      if (raycaster) {
        raycaster->setBackground(r,g,b,a);
      }
    }

    EMSCRIPTEN_KEEPALIVE int runGraphicsScript(const char* src) {
      if (!raycaster || !src) {
        return static_cast<int>(CommandResultCode::invalidArguments);
      }
      return static_cast<int>(raycaster->runGraphicsScript(std::string(src)));
    }

    EMSCRIPTEN_KEEPALIVE void resetRaycaster() {
      if (raycaster) {
        raycaster->resetState();
      }
    }

    EM_JS(void, jsSetFps, (double fps), {
      if (typeof window.setFpsText === "function") {
        window.setFpsText(fps);
      }
    });

    EM_JS(void, jsSetMessage, (const char* msg), {
      const s = UTF8ToString(msg);
      if (typeof window.setWasmMessage === "function") {
        window.setWasmMessage(s);
      }
    });

    EM_JS(void, jsSetTF, (int gaussian, double start, double width), {
      if (typeof window.setWasmTf === "function") {
        window.setWasmTf(gaussian, start, width);
      }
    });

    EM_JS(void, jsSetCustomTF, (const char* encodedTransform), {
      const s = UTF8ToString(encodedTransform);
      if (typeof window.setWasmCustomTf === "function") {
        window.setWasmCustomTf(s);
      }
    });

    EM_JS(void, jsAlphaThreshold, (double a), {
      if (typeof window.setWasmAlphaThreshold === "function") {
        window.setWasmAlphaThreshold(a);
      }
    });

    EM_JS(void, jsSetTransform, (const char* transStr), {
      const s = UTF8ToString(transStr);
      if (typeof window.setWasmTransform === "function") {
        window.setWasmTransform(s);
      }
    });

    EM_JS(void, jsSetMode, (int modeIndex), {
      if (typeof window.setMode === "function") {
        window.setMode(modeIndex);
      }
    });

    EM_JS(void, jsSetVolume, (int volumeIndex), {
      if (typeof window.setVolume === "function") {
        window.setVolume(volumeIndex);
      }
    });

    EM_JS(void, jsSetLevel, (int level), {
      if (typeof window.setLevel === "function") {
        window.setLevel(level);
      }
    });

    EM_JS(void, jsUseOrtho, (bool useOrtho), {
      if (typeof window.setUseOrtho === "function") {
        window.setUseOrtho(useOrtho);
      }
    });

    static double lastfps = -1;
    static void forwardFpsToJs(double fps) {
      if (fps != lastfps) {
        jsSetFps(fps);
        lastfps = fps;
      }
    }

    static void forwardMessageToJs(const std::string& s) {
      jsSetMessage(s.c_str());
    }

    static void forwardTransformToJs(const std::string& s) {
      jsSetTransform(s.c_str());
    }

    static void forwardCustomTFToJs(const std::string& s) {
      jsSetCustomTF(s.c_str());
    }

    static void forwardModeToJs(int mode) {
      jsSetMode(mode);
    }

    static void forwardAlphaThreshold(float a) {
      jsAlphaThreshold(a);
    }

    static void forwardVolumeToJs(int volume) {
      jsSetVolume(volume);
    }

    static void forwardTFToJs(bool gaussian, double start, double width) {
      jsSetTF(gaussian ? 1 : 0, start, width);
    }

    static void forwardLevelToJs(int level) {
      jsSetLevel(level);
    }

    static void forwardUseOrthoToJs(bool useOrtho) {
      jsUseOrtho(useOrtho);
    }
}

int main(int argc, char** argv) {
  try {
    raycaster = std::make_shared<Raycaster>();
    raycaster->setCallbacks(forwardFpsToJs, forwardMessageToJs,
                            forwardTransformToJs, forwardTFToJs,
                            forwardCustomTFToJs, forwardAlphaThreshold,
                            forwardModeToJs, forwardVolumeToJs,
                            forwardLevelToJs, forwardUseOrthoToJs);
    raycaster->run();
  }
  catch (const GLException& e) {
    std::stringstream ss;
    ss << "Insufficient OpenGL Support " << e.what();
    std::cerr << ss.str().c_str() << std::endl;
    return EXIT_FAILURE;
  }
  return EXIT_SUCCESS;
}

#endif

  /*
   Copyright (c) 2026 Computer Graphics and Visualization Group, University of
   Duisburg-Essen

   Permission is hereby granted, free of charge, to any person obtaining a copy of
   this software and associated documentation files (the "Software"), to deal in the
   Software without restriction, including without limitation the rights to use, copy,
   modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and
   to permit persons to whom the Software is furnished to do so, subject to the following
   conditions:

   The above copyright notice and this permission notice shall be included in all copies
   or substantial portions of the Software.

   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
   INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
   PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
   HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF
   CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR
   THE USE OR OTHER DEALINGS IN THE SOFTWARE.
   */
