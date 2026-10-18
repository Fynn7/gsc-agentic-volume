void Raycaster::registerCommands() {
  // Local rebuild addition: actual native scene readback, no semantic assertions.
  interpreter.registerCommand("dumpstate", [this](std::string filename) -> CommandResultCode {
    std::ofstream out(filename);
    if (!out) return CommandResultCode::callbackError;
    out << std::setprecision(9) << "{\"tfStart\":" << tfStart << ",\"tfWidth\":" << tfWidth
        << ",\"dimensions\":[" << voxelCount.x << "," << voxelCount.y << "," << voxelCount.z
        << "],\"translation\":[" << translation.x << "," << translation.y << "," << translation.z
        << "],\"rotation\":[";
    for (int r=0;r<4;++r) for(int c=0;c<4;++c) { if(r||c) out << ","; out << rotation[r*4+c]; }
    out << "],\"crop\":[";
    const float n[3]={voxelCount.x,voxelCount.y,voxelCount.z};
    const float sz[3]={clipBoxSize.x,clipBoxSize.y,clipBoxSize.z};
    const float sh[3]={clipBoxShift.x,clipBoxShift.y,clipBoxShift.z};
    for(int i=0;i<3;++i) { if(i) out << ","; out << (sh[i]+.5f-sz[i]/2)*n[i] << "," << (sh[i]+.5f+sz[i]/2)*n[i]; }
    out << "]}";
    return out.good() ? CommandResultCode::success : CommandResultCode::callbackError;
  });

  interpreter.registerCommand("setclipbounds", [this](float x0,float x1,float y0,float y1,float z0,float z1) {
    return setClipBounds(x0,x1,y0,y1,z0,z1);
  });
  interpreter.registerCommand("resetclipbounds", [this]() {
    return setClipBounds(0,voxelCount.x,0,voxelCount.y,0,voxelCount.z);
  });
  interpreter.registerCommand("constantSampleCount",
  [this](bool enabled) {
    setConstantSampleCount(enabled);
  }
  );
  interpreter.registerCommand("setrate",
  [this](float f) -> CommandResultCode {
    if (f <= 0.0f) {
      return CommandResultCode::invalidArguments;
    }
    setSampling(f);
    return CommandResultCode::success;
  }
  );
  interpreter.registerCommand("setsubdiv",
  [this](uint32_t i) -> CommandResultCode {
    if (i == 0) {
      return CommandResultCode::invalidArguments;
    }
    setVirtualSamples(i);
    return CommandResultCode::success;
  }
  );
  interpreter.registerCommand("setuseortho",
  [this](bool useOrtho) {
    setUseOrtho(useOrtho);
  }
  );
  interpreter.registerCommand("setusennfilter",
  [this](bool useNNFilter) {
    setUseNNFilter(useNNFilter);
  }
  );
  interpreter.registerCommand("setmethod",
  [this](uint32_t i) -> CommandResultCode {
    if (i >= renderModes.size()) {
      return CommandResultCode::invalidArguments;
    }
    setRendermode(i);
    return CommandResultCode::success;
  }
  );
  interpreter.registerCommand("setvolume",
  [this](uint32_t i) -> CommandResultCode {
    if (i >= volumes.size()) {
      return CommandResultCode::invalidArguments;
    }
    setVolume(i);
    return CommandResultCode::success;
  }
  );
  interpreter.registerCommand("loadvolume",
  [this](const std::string& path) -> CommandResultCode {
    return loadVolumePath(path);
  }
  );
  interpreter.registerCommand("settffile",
  [this](const std::string& filename) {
    loadTFFile(filename);
  }
  );
  interpreter.registerCommand("settfcode",
  [this](const std::string& code) {
    setCustomTF(code);
  }
  );
  interpreter.registerCommand("settfparams",
  [this](bool g, float s, float w) {
    setTFParams(g, s, w);
  }
  );
  interpreter.registerCommand("resetrotation",
  [this]() {
    rotation = Mat4{}  ;
    updateMatrices();
  }
  );
  interpreter.registerCommand("prefilter",
  [this]() {
    computeAllPrefilter();
  }
  );
  interpreter.registerCommand("setLevel",
  [this](uint32_t l) {
    if(!setLevel(l)) return CommandResultCode::invalidArguments;
    return CommandResultCode::success;
  }
  );
  interpreter.registerCommand("setrotation",
  [this](float v11, float v12, float v13, float v14,
  float v21, float v22, float v23, float v24,
  float v31, float v32, float v33, float v34,
  float v41, float v42, float v43, float v44) {
    rotation = Mat4{v11, v12, v13, v14,
      v21, v22, v23, v24,
      v31, v32, v33, v34,
      v41, v42, v43, v44};
    updateMatrices();
  }
  );
  interpreter.registerCommand("addrotationx",
  [this](float degree) {
    rotation = Mat4::rotationX(degree) * rotation;
    updateMatrices();
  }
  );
  interpreter.registerCommand("addrotationy",
  [this](float degree) {
    rotation = Mat4::rotationY(degree) * rotation;
    updateMatrices();
  }
  );
  interpreter.registerCommand("addrotationz",
  [this](float degree) {
    rotation = Mat4::rotationZ(degree) * rotation;
    updateMatrices();
  }
  );
  interpreter.registerCommand("settranslation",
  [this](float x,float y,float z) {
    this->translation = Vec3(x,y,z);
    updateMatrices();
  }
  );
  interpreter.registerCommand("setalphathreshold",
  [this](float threshold) {
    setAlphaThreshold(threshold);
  }
  );
  interpreter.registerCommand("settransformparams",
  [this](const std::string& transform) {
    setTransformParams(transform);
  }
  );
}

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
