#pragma once

#include <string>
#include <vector>

#include <GLProgram.h>

enum class VolumeType {
  RAW,
  QUADRATIC,
  CUBIC,
  NONE
};

enum class AdditionalTables {
  PRE_INTEGRATION,
  PEAK_FINDING,
  NONE
};

class RenderMode {
public:
  std::string description;
  GLProgram program;
  bool usesLighting;
  bool virtualSampling;
  AdditionalTables additionalTables;
  VolumeType volumeType;

  RenderMode(const std::string& description,
             const std::string& reconstructionShaderName,
             const std::string& traversalShaderName,
             bool usesLighting,
             bool virtualSampling,
             AdditionalTables additionalTables,
             VolumeType volumeType) :
  description(description),
  program(GLProgram::createFromFiles(std::vector<std::string>{"vertexShader.glsl"},
                                     std::vector<std::string>{"common.glsl", reconstructionShaderName, traversalShaderName},
                                     std::vector<std::string>{},
                                     true, true)),
  usesLighting(usesLighting),
  virtualSampling(virtualSampling),
  additionalTables(additionalTables),
  volumeType(volumeType)
  {
  }
};

std::vector<RenderMode> makeRenderModes();

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
