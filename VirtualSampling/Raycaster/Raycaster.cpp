#include "Raycaster.h"
#include "Clipper.h"
#include <cmath>

#include "RaycasterScript.inc"

Raycaster::Raycaster(const std::vector<std::string>& args) :
GLApp(800, 600, 1, "Raycaster", true, false, true, args),
renderModes(makeRenderModes())
{
  registerCommands();
}

CommandResultCode Raycaster::runGraphicsScript(const std::string& source) {
  return runScriptFromString(source);
}

void Raycaster::resetState() {
  reset();
}

CommandResultCode Raycaster::setClipBounds(float x0, float x1, float y0, float y1, float z0, float z1) {
  const float lows[3] = {x0,y0,z0}, highs[3] = {x1,y1,z1};
  const float counts[3] = {voxelCount.x,voxelCount.y,voxelCount.z};
  for (int axis=0; axis<3; ++axis) {
    if (!std::isfinite(lows[axis]) || !std::isfinite(highs[axis]) ||
        counts[axis]<=0 || lows[axis]<0 || highs[axis]>counts[axis] || lows[axis]>=highs[axis])
      return CommandResultCode::invalidArguments;
  }
  clipBoxSize = Vec3{(x1-x0)/voxelCount.x,(y1-y0)/voxelCount.y,(z1-z0)/voxelCount.z};
  clipUsesVoxelBounds = true;
  clipBoxShift = Vec3{(x0+x1)/(2*voxelCount.x)-0.5f,
                     (y0+y1)/(2*voxelCount.y)-0.5f,
                     (z0+z1)/(2*voxelCount.z)-0.5f};
  updateMatrices();
  return CommandResultCode::success;
}
void Raycaster::resetClipBounds() {
  clipUsesVoxelBounds = false;
  clipBoxSize = Vec3{1,1,1};
  clipBoxShift = Vec3{0,0,0};
}

void Raycaster::updateVolume() {
  const RenderMode& renderMode = renderModes[currentRenderModeIndex];
  const VolumeMetaData& volumeMeta = volumes[currentVolumeIndex].getMeta();

  voxelCount = Vec3{
    float(volumeMeta.width),
    float(volumeMeta.height),
    float(volumeMeta.depth)
  };
  volumeExtend = volumeMeta.scale*voxelCount/float(volumeMeta.maxSize());
  if (renderMode.volumeType != VolumeType::NONE) {
    auto [volumeData, format] = volumes[currentVolumeIndex].getVolumeData(renderMode.volumeType);
    volumeTex.setData(volumeData,
                      uint32_t(volumeMeta.width),
                      uint32_t(volumeMeta.height),
                      uint32_t(volumeMeta.depth),
                      1, format);
  }
  setSampling(oversampling);

  transferFunction
    .setPreIntegration(renderMode.additionalTables == AdditionalTables::PRE_INTEGRATION);
  transferFunction
    .setPeakFinding(renderMode.additionalTables == AdditionalTables::PEAK_FINDING);

  updateMatrices();
}

void Raycaster::setUseOrtho(bool newUseOrtho) {
  if (useOrthoCallback && newUseOrtho != useOrtho ) {
    useOrthoCallback(newUseOrtho);
  }
  useOrtho = newUseOrtho;
  updateMatrices();
}

void Raycaster::updateMatrices() {
  const Dimensions fbDim = glEnv.getFramebufferSize();
  if (useOrtho) {
    projection = Mat4::ortho(-0.5f*fbDim.aspect(),
                             0.5f*fbDim.aspect(),
                             -0.5f, 0.5f, near, 100.0f);
  } else {
    projection = Mat4{ Mat4::perspective(45.0f, fbDim.aspect(), near, 100.0f) };
  }


  clipBoxShift = Vec3::maxV((Vec3{1,1,1}-clipBoxSize)*-0.5,Vec3::minV((Vec3{1,1,1}-clipBoxSize)*0.5, clipBoxShift));
  const Vec3 voxelShift = (voxelCount.x > 0 &&
                           voxelCount.y > 0 &&
                           voxelCount.z > 0) ? Vec3{0.5f}/voxelCount : Vec3{0,0,0};

  clipBox = Mat4::translation(clipBoxShift-(clipUsesVoxelBounds ? Vec3{0,0,0} : voxelShift)) * Mat4::scaling(clipBoxSize);
  minBounds = clipBox * Vec3{-0.5,-0.5,-0.5} + 0.5f;
  maxBounds = clipBox * Vec3{ 0.5, 0.5, 0.5} + 0.5f;

  model = Mat4::translation(translation) * rotation * Mat4::translation(voxelShift) * Mat4::scaling(volumeExtend);
  modelView = view * model * clipBox;

  modelViewIT = Mat4::transpose(Mat4::inverse(modelView));
  modelViewProjection = projection * modelView;
  viewToTexture = Mat4::translation({0.5f,0.5f,0.5f}) * Mat4::inverse(view * model);
  meshNeedsUpdate = true;
}

void Raycaster::clipCubeToNearplane() {
  if (!meshNeedsUpdate) return;

  const Mat4 clipModel = Mat4::translation(translation) * rotation * Mat4::scaling(volumeExtend);
  // transpose( inverse( inverse(view*model) ) ) -> transpose(view*model)
  const Vec4 objectSpaceNearPlane{Mat4::transpose(view*clipModel)*Vec4{0,0,1.0f,near+0.01f}};
  std::vector<float> verts = Clipper::meshPlane(cube.getVertices(),
                                                objectSpaceNearPlane.xyz,
                                                objectSpaceNearPlane.w);
  vertCount = verts.size()/3;
  vbCube.setData(verts, 3);
  meshNeedsUpdate = false;
}

void Raycaster::updateTransferFunction() {
  switch (tfType) {
    case TFType::GAUSSIAN :
      transferFunction.gaussian(TransferFunction::Channel::R, tfStart, tfWidth);
      transferFunction.gaussian(TransferFunction::Channel::G, tfStart, tfWidth);
      transferFunction.gaussian(TransferFunction::Channel::B, tfStart, tfWidth);
      transferFunction.gaussian(TransferFunction::Channel::A, tfStart, tfWidth);
      break;
    case TFType::SMOOTHSTEP :
      transferFunction.smoothStep(TransferFunction::Channel::R, tfStart, tfWidth);
      transferFunction.smoothStep(TransferFunction::Channel::G, tfStart, tfWidth);
      transferFunction.smoothStep(TransferFunction::Channel::B, tfStart, tfWidth);
      transferFunction.smoothStep(TransferFunction::Channel::A, tfStart, tfWidth);
      break;
    default:
      break;
  }
}

void Raycaster::reset() {
  useOrtho = false;
  useNNFilter = false;
  rotation = Mat4{};
  setTFParams(false, 0.1f, 0.12f);
  translation = Vec3{};
  near = 0.1f;
  subdiv = 1;
  oversampling = 1.0f;
  constantSampleCount = false;
  clipBoxSize = Vec3{1,1,1};
  clipBoxShift = Vec3{0,0,0};
  updateTitle();
  updateVolume();
  setUseNNFilter(useNNFilter);
}


std::string Raycaster::genTransformString() const {
  std::ostringstream s;
  s.imbue(std::locale::classic());
  s << std::setprecision(std::numeric_limits<float>::max_digits10);
  s << rotation.toEncoding() << "~" << translation.toEncoding();
  return s.str();
}

void Raycaster::printURL() const {
  std::ostringstream s;
  s.imbue(std::locale::classic());
  s << std::setprecision(std::numeric_limits<float>::max_digits10);
  s << "?dataset=" << currentVolumeIndex;
  s << "&level=" << volumes[currentVolumeIndex].getLevel();
  s << "&method=" << currentRenderModeIndex;
  s << "&const=" << constantSampleCount;
  s << "&ortho=" << useOrtho;
  s << "&vs=" << subdiv;
  s << "&sr=" << int(std::log2(oversampling));
  s << "&tfType=" << int(tfType);
  if (tfType == TFType::CUSTOM) {
    s << "&tfEncoding=" << transferFunction.encodeForUrl();
  } else {
    s << "&tfStart=" << tfStart;
    s << "&tfWidth=" << tfWidth;
  }
  s << "&transform=" << genTransformString();
  s << "&background=" << getBackground().x << "," << getBackground().y << "," << getBackground().z << "," << getBackground().w;
  std::cout << s.str() << std::endl;
}

void Raycaster::init() {
  vertCount = cube.getVertices().size()/3;
  cubeArray.bind();
  vbCube.setData(cube.getVertices(), 3);
  cubeArray.connectVertexAttrib(vbCube, renderModes[currentRenderModeIndex].program, "vPos", 3);
  setBackground(0.3f,0.3f,0.8f,1.0f);

  GL(glEnable(GL_CULL_FACE));
  GL(glCullFace(GL_BACK));
  GL(glEnable(GL_BLEND));
  GL(glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA));
  GL(glBlendEquation(GL_FUNC_ADD));
}

void Raycaster::resize(const Dimensions winDim, const Dimensions fbDim) {
  GLApp::resize(winDim, fbDim);
  arcball.setWindowSize({winDim.width,winDim.height});
  updateMatrices();
}

void Raycaster::setupShader() {
  const RenderMode& renderMode = renderModes[currentRenderModeIndex];
  const GLProgram& currentProgram = renderMode.program;
  
  currentProgram.enable();

  currentProgram.setUniform("modelViewProjection", modelViewProjection);
  currentProgram.setUniform("clip", clipBox);
  currentProgram.setUniform("minBounds", minBounds);
  currentProgram.setUniform("maxBounds", maxBounds);
  if (useOrtho) {
    currentProgram.setUniform("cameraPosInTextureSpace", (viewToTexture * Vec4{0,0,100000000,1}).xyz);
  } else {
    currentProgram.setUniform("cameraPosInTextureSpace", (viewToTexture * Vec4{0,0,0,1}).xyz);
  }

  currentProgram.setUniform("voxelCount", voxelCount);
  currentProgram.setUniform("alphaThreshold",alphaThreshold);

  switch (renderMode.volumeType) {
    case VolumeType::RAW:
    case VolumeType::QUADRATIC:
    case VolumeType::CUBIC:
      currentProgram.setTexture("volume",volumeTex,0);
      break;
    case VolumeType::NONE:
      currentProgram.setUniform("volumeIndex", int(currentVolumeIndex));
      break;
    default:
      break;
  }
  
  if (renderMode.usesLighting) {
    currentProgram.setUniform("modelView", modelView);
    currentProgram.setUniform("modelViewIT", modelViewIT);
  }

  switch (renderMode.additionalTables) {
    case AdditionalTables::PRE_INTEGRATION: {
      const GLTexture2D& tex = transferFunction.getPreIntegratedTexture();
      currentProgram.setTexture("transferPre",tex,2);
      currentProgram.setUniform("tfSize", Vec2(float(tex.getWidth()), float(tex.getHeight())));
      break;
    }
    case AdditionalTables::PEAK_FINDING: {
      currentProgram.setTexture("transfer",transferFunction.getTexture(),1);
      const GLTexture2D& tex = transferFunction.getPeakFindingTexture();
      currentProgram.setTexture("peakTable",tex,2);
      currentProgram.setUniform("tfSize", Vec2(float(tex.getWidth()), float(tex.getHeight())));
      break;
    }
    default:
      currentProgram.setTexture("transfer",transferFunction.getTexture(),1);
      break;
  }


  if (renderMode.virtualSampling) {
    if (constantSampleCount) {
      currentProgram.setUniform("oversampling", oversampling/float(1+subdiv));
    } else {
      currentProgram.setUniform("oversampling", oversampling);
    }
    currentProgram.setUniform("subdiv",int(subdiv));
  } else {
    currentProgram.setUniform("oversampling", oversampling);
  }
}

void Raycaster::animate(double animationTime) {
  clipCubeToNearplane();
  setupShader();  
}

void Raycaster::draw() {
  cubeArray.bind();
  GL(glDrawArrays(GL_TRIANGLES, 0, GLsizei(vertCount)));

  if (tfEditor) drawTransferFunctionEditor();
}

void Raycaster::setRendermode(uint32_t index) {
  const uint32_t clampedIndex = index % renderModes.size();

  if (modeCallback && clampedIndex != currentRenderModeIndex) {
    modeCallback(int(clampedIndex));
  }

  previousRenderModeIndex = currentRenderModeIndex;
  currentRenderModeIndex = clampedIndex;
  updateVolume();
  updateTitle();
}

void Raycaster::setVolume(uint32_t index) {
  resetClipBounds();
  const uint32_t clampedIndex = index % volumes.size();

  if (volumeCallback && clampedIndex != currentVolumeIndex) {
    volumeCallback(int(clampedIndex));
  }

  currentVolumeIndex = clampedIndex;
  updateVolume();
  updateTitle();
}

CommandResultCode Raycaster::loadVolumePath(const std::string& datPath) {
  if (datPath.empty() || datPath.find(' ') != std::string::npos) {
    return CommandResultCode::invalidArguments;
  }
  try {
    QVis qvis{datPath};
    volumes.emplace_back(VolumeListElement{std::move(qvis.volume)});
    setVolume(uint32_t(volumes.size() - 1));
    return CommandResultCode::success;
  } catch (const QVisFileException& e) {
    std::cout << "loadvolume failed: " << e.what() << std::endl;
    return CommandResultCode::invalidArguments;
  }
}

void Raycaster::setConstantSampleCount(bool value) {
  constantSampleCount = value;
}

void Raycaster::setVirtualSamples(uint32_t index) {
  subdiv = index;
  updateTitle();
}

void Raycaster::setSampling(float value) {
  oversampling = value;
  updateTitle();

  // usage of voxelCount.x here assumes that
  // voxelCountx == voxelCount.y == voxelCount.z
  const float delta = 200.0f/(voxelCount.x*oversampling);
  transferFunction.setDelta(delta);
}

void Raycaster::setTFParams(bool gaussian, float start, float width) {
  tfType = gaussian ? TFType::GAUSSIAN : TFType::SMOOTHSTEP;
  tfStart = start;
  tfWidth = std::max(0.00001f,width);
  updateTransferFunction();
}

void Raycaster::loadTFFile(const std::string& filename) {
  try {
    transferFunction.load(filename);
    tfType = TFType::CUSTOM;
  } catch (...) {
    // if URL is invalid, don't change anything
  }
}

void Raycaster::setCustomTF(const std::string& encodedTF) {
  try {
    transferFunction.decodeFromURL(encodedTF);
    tfType = TFType::CUSTOM;
  } catch (...) {
    // if URL is invalid, don't change anything
  }
}

void Raycaster::setTransformParams(const std::string& transformStr) {
  const auto pos = transformStr.rfind('~');
  if (pos == std::string::npos) return;
  const std::string rotationStr = transformStr.substr(0, pos);
  const std::string translationStr = transformStr .substr(pos + 1);

  rotation = Mat4::fromEncoding(rotationStr);
  translation = Vec3::fromEncoding(translationStr);

  updateMatrices();
}

bool Raycaster::setLevel(size_t level) {
  const size_t prevLevel = volumes[currentVolumeIndex].getLevel();

  if (!volumes[currentVolumeIndex].setLevel(level)) return false;
  updateVolume();

  if (levelCallback && level != prevLevel ) {
    levelCallback(int(level));
  }

  return true;
}

void Raycaster::setUseNNFilter(bool useNNFilter) {
  this->useNNFilter = useNNFilter;
  if (useNNFilter) {
    volumeTex.setFilter(GL_NEAREST, GL_NEAREST);
  } else {
    volumeTex.setFilter(GL_LINEAR, GL_LINEAR);
  }
}

void Raycaster::computeAllPrefilter() {
  for (auto& volume : volumes) {
    const size_t currentLevel = volume.getLevel();
    volume.setLevel(0);
    do {
      volume.getVolumeData(VolumeType::QUADRATIC);
      volume.getVolumeData(VolumeType::CUBIC);
    } while (volume.setLevel(volume.getLevel()+1));
    volume.setLevel(currentLevel);
  }
}

void Raycaster::keyboard(int key, int scancode, int action, int mods) {
  if (action == GLENV_PRESS) {
    switch (key) {
      case GLENV_KEY_0 : case GLENV_KEY_1 : case GLENV_KEY_2 :
      case GLENV_KEY_3 : case GLENV_KEY_4 : case GLENV_KEY_5 :
      case GLENV_KEY_6 : case GLENV_KEY_7 : case GLENV_KEY_8 :
      case GLENV_KEY_9 :
        numberEntered = numberEntered*10+uint32_t(key-GLENV_KEY_0);
        break;
      case GLENV_KEY_ENTER:
        setRendermode(numberEntered);
        numberEntered = 0;
        break;
      case GLENV_KEY_ESCAPE :
        closeWindow();
        break;
      case GLENV_KEY_O:
        setUseOrtho(!useOrtho);
        break;
      case GLENV_KEY_V:
        if (mods & GLENV_MOD_SHIFT) {
          setVolume(uint32_t(currentVolumeIndex+volumes.size()-1));
        } else {
          setVolume(uint32_t(currentVolumeIndex)+1);
        }
        break;
      case GLENV_KEY_J:
        computeAllPrefilter();
        break;
      case GLENV_KEY_C:
        setConstantSampleCount(!constantSampleCount);
        break;
      case GLENV_KEY_P:
        setRendermode(previousRenderModeIndex);
        break;
      case GLENV_KEY_N:
        setRendermode(currentRenderModeIndex+uint32_t(renderModes.size()-1));
        break;
      case GLENV_KEY_M:
        setRendermode(currentRenderModeIndex+1);
        break;
      case GLENV_KEY_W:
        setSampling(oversampling * 2);
        break;
      case GLENV_KEY_Q:
        setSampling(oversampling / 2);
        break;
      case GLENV_KEY_R:
        reset();
        break;
      case GLENV_KEY_D:
        if (mods & GLENV_MOD_SHIFT) {
          if (volumes[currentVolumeIndex].getLevel() > 0)
            setLevel( volumes[currentVolumeIndex].getLevel() - 1 );
        } else {
          setLevel( volumes[currentVolumeIndex].getLevel() + 1 );
        }
        break;
      case GLENV_KEY_U:
        printURL();
        break;
      case GLENV_KEY_E:
        if (alphaThreshold < 1.0f)
          setAlphaThreshold(2.0f);
        else
          setAlphaThreshold(0.99f);
        break;
      case GLENV_KEY_A:
        activeChannel = TransferFunction::Channel((int(activeChannel)+1)%4);
        break;
      case GLENV_KEY_F:
        setUseNNFilter(!useNNFilter);
        break;
      case GLENV_KEY_T:
        tfEditor = !tfEditor;
        if (tfEditor) {
          tfType = TFType::CUSTOM;
        }
        break;
      case GLENV_KEY_G: {
        const bool toggleToGaussian = (tfType == TFType::SMOOTHSTEP);
        setTFParams(toggleToGaussian, tfStart, tfWidth);
        if (tfCallback) tfCallback(toggleToGaussian,
                                   tfStart,
                                   tfWidth);
        break;
      }
      case GLENV_KEY_UP:
        if (mods & GLENV_MOD_SHIFT) {
          translation.y += 0.1f;
        } else {
          translation.z -= 0.1f;
        }
        updateMatrices();
        if (transformCallback) transformCallback(genTransformString());
        break;
      case GLENV_KEY_DOWN:
        if (mods & GLENV_MOD_SHIFT) {
          translation.y -= 0.1f;
        } else {
          translation.z += 0.1f;
        }
        updateMatrices();
        if (transformCallback) transformCallback(genTransformString());
        break;
      case GLENV_KEY_LEFT:
        if (mods & GLENV_MOD_SHIFT) {
          translation.x -= 0.1f;
          updateMatrices();
          if (transformCallback) transformCallback(genTransformString());
        } else {
          setVirtualSamples(std::max<uint32_t>(1, subdiv-1));
        }
        break;
      case GLENV_KEY_RIGHT:
        if (mods & GLENV_MOD_SHIFT) {
          translation.x += 0.1f;
          updateMatrices();
          if (transformCallback) transformCallback(genTransformString());
        } else {
          setVirtualSamples(subdiv+1);
        }
        break;
    }

  }
  switch (key) {
    case GLENV_KEY_F1:
      if (mods & GLENV_MOD_SHIFT)
        clipBoxSize.x += 0.01f;
      else
        clipBoxSize.x -= 0.01f;
      updateMatrices();
      break;
    case GLENV_KEY_F2:
      if (mods & GLENV_MOD_SHIFT)
        clipBoxSize.y += 0.01f;
      else
        clipBoxSize.y -= 0.01f;
      updateMatrices();
      break;
    case GLENV_KEY_F3:
      if (mods & GLENV_MOD_SHIFT)
        clipBoxSize.z += 0.01f;
      else
        clipBoxSize.z -= 0.01f;
      updateMatrices();
      break;
    case GLENV_KEY_F4:
      if (mods & GLENV_MOD_SHIFT)
        clipBoxShift.x += 0.01f;
      else
        clipBoxShift.x -= 0.01f;
      updateMatrices();
      break;
    case GLENV_KEY_F5:
      if (mods & GLENV_MOD_SHIFT)
        clipBoxShift.y += 0.01f;
      else
        clipBoxShift.y -= 0.01f;
      updateMatrices();
      break;
    case GLENV_KEY_F6:
      if (mods & GLENV_MOD_SHIFT)
        clipBoxShift.z += 0.01f;
      else
        clipBoxShift.z -= 0.01f;
      updateMatrices();
      break;
  }
}

void Raycaster::mouseMove(double xPosition, double yPosition) {
  if (rightMouseDown) {
    const Dimensions dim = glEnv.getFramebufferSize();
    const double xDelta = xPositionStart - xPosition;
    const double yDelta = yPositionStart - yPosition;
    xPositionStart = xPosition;
    yPositionStart = yPosition;
    
    setTFParams(tfType == TFType::GAUSSIAN,
                tfStart+float(xDelta/dim.width),
                tfWidth+float(yDelta/dim.height));
  }
  
  if (leftMouseDown) {
    if (tfEditor) {
      const Vec2 bias  = (br)/2.0f+0.5f;
      const Vec2 scale = (tl-br)/2.0f;
      const Dimensions dim = glEnv.getWindowSize();
      const float x = (float(xPosition/dim.width)-bias.x)/scale.x;
      const float y = ((1.0f-float(yPosition/dim.height))-bias.y)/scale.y;
      transferFunction.setValue(x, y, activeChannel);
    } else {
      const Quaternion q = arcball.drag({uint32_t(xPosition),uint32_t(yPosition)});
      rotation = q.computeRotation() * rotation;
      updateMatrices();
    }
  }
}

void Raycaster::mouseButton(int button, int state, int mods, double xPosition, double yPosition) {
  if (button == GLENV_MOUSE_BUTTON_RIGHT) {
    if (tfType == TFType::CUSTOM) {
      tfType = TFType::SMOOTHSTEP;
    }
    rightMouseDown = state == GLENV_MOUSE_PRESS;
    xPositionStart = xPosition;
    yPositionStart = yPosition;
    if (state == GLENV_MOUSE_RELEASE) {
      if (tfCallback) {
        tfCallback(tfType == TFType::GAUSSIAN, tfStart, tfWidth);
      }
    }
  }
  
  if (button == GLENV_MOUSE_BUTTON_LEFT) {
    leftMouseDown = state == GLENV_MOUSE_PRESS;
    arcball.click({uint32_t(xPosition),uint32_t(yPosition)});
    if (state == GLENV_MOUSE_RELEASE) {
      if (transformCallback) {
        transformCallback(genTransformString());
      }
      if (tfEditor && customTfCallback) {
        customTfCallback(transferFunction.encodeForUrl());
      }
    }
  }
}

void Raycaster::updateTitle(){
  std::stringstream ss;
  ss << currentRenderModeIndex << ": ";
  ss << renderModes[currentRenderModeIndex].description
  << " (" << oversampling << " x sampling";

  if (renderModes[currentRenderModeIndex].virtualSampling) {
    ss << " + " << subdiv << " virtual samples";
  }

  ss << ")";
  glEnv.setTitle(ss.str());

  if (messageCallback) messageCallback("Rendering");
}

void Raycaster::setCallbacks(std::function<void(double)> fpsCallback,
                             std::function<void(const std::string&)> messageCallback,
                             std::function<void(const std::string&)> transformCallback,
                             std::function<void(bool, double, double)> tfCallback,
                             std::function<void(const std::string&)> customTfCallback,
                             std::function<void(double)> alphaCallback,
                             std::function<void(int)> modeCallback,
                             std::function<void(int)> volumeCallback,
                             std::function<void(int)> levelCallback,
                             std::function<void(bool)> useOrthoCallback) {
  GLApp::setCallbacks(fpsCallback, messageCallback);
  this->transformCallback = transformCallback;
  this->tfCallback = tfCallback;
  this->customTfCallback = customTfCallback;
  this->alphaCallback = alphaCallback;
  this->modeCallback = modeCallback;
  this->volumeCallback = volumeCallback;
  this->levelCallback = levelCallback;
  this->useOrthoCallback = useOrthoCallback;
}

void Raycaster::drawTransferFunctionEditor() {
  drawRect(Vec4{0.0f,0.0f,0.0f,0.3f},br,tl);
  const std::vector<float> frame{
    br.x, br.y, 0.0f, 1.0f, 1.0f, 0.0f, 1.0f,
    br.x, tl.y, 0.0f, 1.0f, 1.0f, 0.0f, 1.0f,
    tl.x, tl.y, 0.0f, 1.0f, 1.0f, 0.0f, 1.0f,
    tl.x, br.y, 0.0f, 1.0f, 1.0f, 0.0f, 1.0f,
  };

  const std::vector<Vec4>& data = transferFunction.getData();

  std::vector<float> r(data.size()*7);
  std::vector<float> g(data.size()*7);
  std::vector<float> b(data.size()*7);
  std::vector<float> a(data.size()*7);

  for (size_t i = 0;i<data.size();++i) {
    const float alpha = float(i)/float(data.size());
    const float xPos = br.x + alpha * (tl.x-br.x);
    const Vec4 elem = data[i];

    r[i*7+0] = xPos; r[i*7+1] = br.y + elem.r * (tl.y-br.y); r[i*7+2] = 0;
    r[i*7+3] = 1; r[i*7+4] = 0; r[i*7+5] = 0; r[i*7+6] = 1.0;

    g[i*7+0] = xPos; g[i*7+1] = br.y + elem.g * (tl.y-br.y); g[i*7+2] = 0;
    g[i*7+3] = 0; g[i*7+4] = 1; g[i*7+5] = 0; g[i*7+6] = 1.0;

    b[i*7+0] = xPos; b[i*7+1] = br.y + elem.b * (tl.y-br.y); b[i*7+2] = 0;
    b[i*7+3] = 0; b[i*7+4] = 0; b[i*7+5] = 1; b[i*7+6] = 1.0;

    a[i*7+0] = xPos; a[i*7+1] = br.y + elem.a * (tl.y-br.y); a[i*7+2] = 0;
    a[i*7+3] = 1; a[i*7+4] = 1; a[i*7+5] = 1; a[i*7+6] = 1.0;
  }

  drawLines(r, LineDrawType::STRIP, activeChannel == TransferFunction::Channel::R ? 2.0f : 1.0f);
  drawLines(g, LineDrawType::STRIP, activeChannel == TransferFunction::Channel::G ? 2.0f : 1.0f);
  drawLines(b, LineDrawType::STRIP, activeChannel == TransferFunction::Channel::B ? 2.0f : 1.0f);
  drawLines(a, LineDrawType::STRIP, activeChannel == TransferFunction::Channel::A ? 2.0f : 1.0f);

  drawLines(frame, LineDrawType::LOOP, 2);
}

void Raycaster::setAlphaThreshold(float threshold) {
  alphaThreshold = threshold;
  if (alphaCallback) alphaCallback(alphaThreshold);
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
