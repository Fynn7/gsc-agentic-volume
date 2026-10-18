#pragma once

#include <GLApp.h>
#include <Tesselation.h>
#include <ArcBall.h>

#include "TransferFunction.h"

#include "VolumeGenerator.h"
#include "VolumeTransformer.h"
#include "QVis.h"
#include "RenderModes.h"

inline Volume<uint8_t> loadVolume(const std::string& filename) {
  try {
    const Volume<uint8_t> vol = QVis{filename}.volume;
   // QVis::save(vol.padToPow2(),filename,true); // code to create compressed, padded volumes
    return vol;
  } catch (const QVisFileException& e) {
    std::cout << "Cannot load volume:" << e.what() << std::endl;
    exit(-1);
  }
}

enum class TFType {
  SMOOTHSTEP = 0,
  GAUSSIAN = 1,
  CUSTOM = 2
};


class VolumeListElement {
public:
  VolumeListElement(Volume<uint8_t> v) :
  activeLevel{0}
  {
    const size_t expectedMax = v.possibleMIPLevels();
    volume.reserve(expectedMax);
    quadraticCoeffs.resize(expectedMax);
    cubicCoeffs.resize(expectedMax);
    volume.push_back(std::move(v));
  }

  std::pair<GLvoid*, GLDataType> getVolumeData(VolumeType type) const {
    switch (type) {
      default:
      case VolumeType::RAW:
        return {(GLvoid*)(volume[activeLevel].data.data()), GLDataType::BYTE};
      case VolumeType::QUADRATIC:
        if (!quadraticCoeffs[activeLevel]) {
          const Volume<float> quadratic = VolumeTransformer::prefilterQuadraticBSpline(volume[activeLevel]);
          quadraticCoeffs[activeLevel] = quadratic.convert<half::Half>().data;
#ifdef VALIDATE_PREFILTER
          const Volume<float> backandForthVolume = quadratic.convert<half::Half>().convert<float>();
          const bool validateHalf = VolumeTransformer::testPrefilterQuad(volume[activeLevel], backandForthVolume);
          if (validateHalf) {
            std::cout << "Quadratic prefilter pipeline Validated" << std::endl;
          } else {
            std::cout << "Quadratic prefilter pipeline error in half data, testing float" << std::endl;
            const bool validateFloat = VolumeTransformer::testPrefilterQuad(volume[activeLevel], quadratic);
            std::cout << "Validation result float = " << validateFloat << std::endl;
          }
#endif
        }
        return {(GLvoid*)(quadraticCoeffs[activeLevel])->data(), GLDataType::HALF};
      case VolumeType::CUBIC:
        if (!cubicCoeffs[activeLevel]) {
          const Volume<float> cubic = VolumeTransformer::prefilterCubicBSpline(volume[activeLevel]);
          cubicCoeffs[activeLevel] = cubic.convert<half::Half>().data;
#ifdef VALIDATE_PREFILTER
          const Volume<float> backandForthVolume = cubic.convert<half::Half>().convert<float>();
          const bool validateHalf = VolumeTransformer::testPrefilterCubic(volume[activeLevel], backandForthVolume);
          if (validateHalf) {
            std::cout << "Cubic prefilter pipeline validated" << std::endl;
          } else {
            std::cout << "Cubic prefilter pipeline error in half data, testing float" << std::endl;
            const bool validateFloat = VolumeTransformer::testPrefilterCubic(volume[activeLevel], cubic);
            std::cout << "Validation result float = " << validateFloat << std::endl;
          }
#endif
        }
        return {(GLvoid*)(cubicCoeffs[activeLevel])->data(), GLDataType::HALF};
    }
  }

  VolumeMetaData getMeta() const {
    return volume[activeLevel].metaData;
  }

  bool setLevel(size_t level) {
    while (level+1 > volume.size()) {
      if (!downsample()) return false;
    }
    activeLevel = level;
    return true;
  }

  size_t getLevel() const {
    return activeLevel;
  }

private:
  mutable std::vector<std::optional<std::vector<half::Half>>> quadraticCoeffs;
  mutable std::vector<std::optional<std::vector<half::Half>>> cubicCoeffs;
  std::vector<Volume<uint8_t>> volume;
  size_t activeLevel;

  bool downsample() {
    try {
      volume.push_back(VolumeTransformer::downBy2(volume[volume.size()-1]));
      return true;
    } catch (...) {
      return false;
    }
  }
};


class Raycaster : public GLApp {
public:
  Raycaster(const std::vector<std::string>& args = {});

  void setCallbacks(std::function<void(double)> fpsCallback,
                    std::function<void(const std::string&)> messageCallback,
                    std::function<void(const std::string&)> transformCallback,
                    std::function<void(bool, double, double)> tfCallback,
                    std::function<void(const std::string&)> customTfCallback,
                    std::function<void(double)> alphaCallback,
                    std::function<void(int)> modeCallback,
                    std::function<void(int)> volumeCallback,
                    std::function<void(int)> levelCallback,
                    std::function<void(bool)> useOrthoCallback);
  uint32_t getRendermode() const {return currentRenderModeIndex;}
  void setRendermode(uint32_t index);
  void setVolume(uint32_t index);
  CommandResultCode loadVolumePath(const std::string& datPath);
  void setConstantSampleCount(bool value);
  void setVirtualSamples(uint32_t index);
  void setSampling(float value);
  void loadTFFile(const std::string& filename);
  void setTFParams(bool gaussian, float start, float width);
  void setCustomTF(const std::string& encodedTF);
  void setTransformParams(const std::string& transformStr);
  void setAlphaThreshold(float threshold);
  bool setLevel(size_t level);
  void setUseOrtho(bool newUseOrtho);
  void setUseNNFilter(bool useNNFilter);
  void computeAllPrefilter();
  CommandResultCode runGraphicsScript(const std::string& source);
  void resetState();

private:
  virtual void reset() override;
  virtual void init() override;
  virtual void resize(const Dimensions winDim, const Dimensions fbDim) override;
  virtual void animate(double animationTime) override;
  virtual void draw() override;
  virtual void keyboard(int key, int scancode, int action, int mods) override;
  virtual void mouseMove(double xPosition, double yPosition) override;
  virtual void mouseButton(int button, int state, int mods, double xPosition, double yPosition) override;

  Tesselation cube{Tesselation::genBrick({0, 0, 0}, {1, 1, 1}).unpack()};
  GLBuffer vbCube{GL_ARRAY_BUFFER};
  GLArray cubeArray;
  std::vector<RenderMode> renderModes;
  size_t vertCount;
  TransferFunction transferFunction{256};
  Vec3 volumeExtend;
  Vec3 voxelCount;
  Vec2 tfSize;
  GLTexture3D volumeTex{GL_LINEAR, GL_LINEAR, GL_CLAMP_TO_EDGE,
    GL_CLAMP_TO_EDGE,GL_CLAMP_TO_EDGE};

  ArcBall arcball{{512, 512}};
  Mat4 rotation;
  Vec3 translation;
  Mat4 clipBox;
  Mat4 modelViewProjection;
  Mat4 modelView;
  Mat4 modelViewIT;
  Mat4 viewToTexture;
  Mat4 model;
  Mat4 view{Mat4::lookAt({ 0, 0, 2 }, { 0, 0, 0 }, { 0, 1, 0 })};
  Mat4 projection;
  bool useOrtho;
  bool useNNFilter;

  Vec3 minBounds;
  Vec3 maxBounds;
  bool clipUsesVoxelBounds = false;
  Vec3 clipBoxSize{1,1,1};
  Vec3 clipBoxShift{0,0,0};
  float oversampling;
  uint32_t subdiv;
  float near;
  bool constantSampleCount;
  bool meshNeedsUpdate{true};
  uint32_t numberEntered{0};

  std::vector<VolumeListElement> volumes{
    VolumeListElement{VolumeGenerator::sphere(256,256,256)},
    VolumeListElement{VolumeGenerator::ramp(256,256,256)},
    VolumeListElement{VolumeGenerator::marschnerLobb(256,256,256)},
    VolumeListElement{loadVolume("c60.dat")},
    VolumeListElement{loadVolume("aneurism.dat")},
    VolumeListElement{loadVolume("bonsai.dat")},
    VolumeListElement{loadVolume("head512.dat")},
    VolumeListElement{loadVolume("xmas.dat")},
  };

  size_t currentVolumeIndex{0};
  float tfStart{0.12f};
  float tfWidth{0.1f};
  TFType tfType{TFType::SMOOTHSTEP};
  bool leftMouseDown{false};
  bool rightMouseDown{false};
  double xPositionStart{0};
  double yPositionStart{0};
  uint32_t previousRenderModeIndex{0};
  uint32_t currentRenderModeIndex{0};
  float alphaThreshold{0.99f};

  bool tfEditor{false};
  const Vec2 br{-0.9f,-0.9f};
  const Vec2 tl{ 0.9f,-0.6f};
  TransferFunction::Channel activeChannel = TransferFunction::Channel::A;

  void updateVolume();
  void registerCommands();
  CommandResultCode setClipBounds(float x0, float x1, float y0, float y1, float z0, float z1);
  void resetClipBounds();
  void updateMatrices();
  void clipCubeToNearplane();
  void updateTransferFunction();
  void setupShader();
  void updateTitle();
  std::string genTransformString() const;
  void printURL() const;
  void drawTransferFunctionEditor();

  std::function<void(const std::string&)> transformCallback;
  std::function<void(bool, double, double)> tfCallback;
  std::function<void(const std::string&)> customTfCallback;
  std::function<void(double)> alphaCallback;
  std::function<void(int)> modeCallback;
  std::function<void(int)> volumeCallback;
  std::function<void(int)> levelCallback;
  std::function<void(bool)> useOrthoCallback;
};

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
