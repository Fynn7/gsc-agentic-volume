#include "RenderModes.h"

std::vector<RenderMode> makeRenderModes() {
  return {
    RenderMode{
      "Linear",
      "linear.glsl",
      "baseline.glsl",
      false,false,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Linear Pre-Integrated",
      "linear.glsl",
      "baselinePre.glsl",
      false,false,
      AdditionalTables::PRE_INTEGRATION,
      VolumeType::RAW
    },
    RenderMode{
      "Linear Peak-Finding",
      "linear.glsl",
      "baselinePeak.glsl",
      false,false,
      AdditionalTables::PEAK_FINDING,
      VolumeType::RAW
    },
    RenderMode{
      "Linear + virtual linear Sampling",
      "linear.glsl",
      "virtLinear.glsl",
      false,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Linear + virtual CR Sampling",
      "linear.glsl",
      "virtCR.glsl",
      false,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Linear + virtual Hermite Sampling",
      "linear.glsl",
      "virtH.glsl",
      false,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Linear + virtual monotone Hermite Sampling",
      "linear.glsl",
      "virtHmonotone.glsl",
      false,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Linear + lighting",
      "linear.glsl",
      "lighting.glsl",
      true,false,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Linear Peak-Finding + lighting",
      "linear.glsl",
      "lightingPeak.glsl",
      true,false,
      AdditionalTables::PEAK_FINDING,
      VolumeType::RAW
    },
    RenderMode{
      "Linear + lighting + virtual linear Sampling",
      "linear.glsl",
      "virtLinearLighting.glsl",
      true,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Linear + lighting + virtual CR Sampling",
      "linear.glsl",
      "virtCRLighting.glsl",
      true,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Linear + lighting + virtual Hermite Sampling",
      "linear.glsl",
      "virtHLighting.glsl",
      true,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Linear + lighting + virtual monotone Hermite Sampling",
      "linear.glsl",
      "virtHmonotoneLighting.glsl",
      true,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Quadratic B-Spline",
      "quadB.glsl",
      "baseline.glsl",
      false,false,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Quadratic B-Spline, Pre-Integrated",
      "quadB.glsl",
      "baselinePre.glsl",
      false,false,
      AdditionalTables::PRE_INTEGRATION,
      VolumeType::RAW
    },
    RenderMode{
      "Quadratic B-Spline Peak-Finding",
      "quadB.glsl",
      "baselinePeak.glsl",
      false,false,
      AdditionalTables::PEAK_FINDING,
      VolumeType::RAW
    },
    RenderMode{
      "Quadratic B-Spline + virtual linear Sampling",
      "quadB.glsl",
      "virtLinear.glsl",
      false,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Quadratic B-Spline + virtual CR Sampling",
      "quadB.glsl",
      "virtCR.glsl",
      false,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Quadratic B-Spline + virtual Hermite Sampling",
      "quadB.glsl",
      "virtH.glsl",
      false,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Quadratic B-Spline + virtual monotone Hermite Sampling",
      "quadB.glsl",
      "virtHmonotone.glsl",
      false,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Quadratic B-Spline + lighting",
      "quadB.glsl",
      "lighting.glsl",
      true,false,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Quadratic B-Spline Peak-Finding + lighting",
      "quadB.glsl",
      "lightingPeak.glsl",
      true,false,
      AdditionalTables::PEAK_FINDING,
      VolumeType::RAW
    },
    RenderMode{
      "Quadratic B-Spline + lighting + virtual linear Sampling",
      "quadB.glsl",
      "virtLinearLighting.glsl",
      true,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Quadratic B-Spline + lighting + virtual CR Sampling",
      "quadB.glsl",
      "virtCRLighting.glsl",
      true,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Quadratic B-Spline + lighting + virtual Hermite Sampling",
      "quadB.glsl",
      "virtHLighting.glsl",
      true,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Quadratic B-Spline + lighting + virtual monotone Hermite Sampling",
      "quadB.glsl",
      "virtHmonotoneLighting.glsl",
      true,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },


    RenderMode{
      "Quadratic B-Spline, Prefiltered",
      "quadB.glsl",
      "baseline.glsl",
      false,false,
      AdditionalTables::NONE,
      VolumeType::QUADRATIC
    },
    RenderMode{
      "Quadratic B-Spline, Pre-Integrated, Prefiltered",
      "quadB.glsl",
      "baselinePre.glsl",
      false,false,
      AdditionalTables::PRE_INTEGRATION,
      VolumeType::QUADRATIC
    },
    RenderMode{
      "Quadratic B-Spline Peak-Finding, Prefiltered",
      "quadB.glsl",
      "baselinePeak.glsl",
      false,false,
      AdditionalTables::PEAK_FINDING,
      VolumeType::QUADRATIC
    },
    RenderMode{
      "Quadratic B-Spline + virtual linear Sampling, Prefiltered",
      "quadB.glsl",
      "virtLinear.glsl",
      false,true,
      AdditionalTables::NONE,
      VolumeType::QUADRATIC
    },
    RenderMode{
      "Quadratic B-Spline + virtual CR Sampling, Prefiltered",
      "quadB.glsl",
      "virtCR.glsl",
      false,true,
      AdditionalTables::NONE,
      VolumeType::QUADRATIC
    },
    RenderMode{
      "Quadratic B-Spline + virtual Hermite Sampling, Prefiltered",
      "quadB.glsl",
      "virtH.glsl",
      false,true,
      AdditionalTables::NONE,
      VolumeType::QUADRATIC
    },
    RenderMode{
      "Quadratic B-Spline + virtual monotone Hermite Sampling, Prefiltered",
      "quadB.glsl",
      "virtHmonotone.glsl",
      false,true,
      AdditionalTables::NONE,
      VolumeType::QUADRATIC
    },
    RenderMode{
      "Quadratic B-Spline + lighting, Prefiltered",
      "quadB.glsl",
      "lighting.glsl",
      true,false,
      AdditionalTables::NONE,
      VolumeType::QUADRATIC
    },
    RenderMode{
      "Quadratic B-Spline Peak-Finding + lighting, Prefiltered",
      "quadB.glsl",
      "lightingPeak.glsl",
      true,false,
      AdditionalTables::PEAK_FINDING,
      VolumeType::QUADRATIC
    },
    RenderMode{
      "Quadratic B-Spline + lighting + virtual linear Sampling, Prefiltered",
      "quadB.glsl",
      "virtLinearLighting.glsl",
      true,true,
      AdditionalTables::NONE,
      VolumeType::QUADRATIC
    },
    RenderMode{
      "Quadratic B-Spline + lighting + virtual CR Sampling, Prefiltered",
      "quadB.glsl",
      "virtCRLighting.glsl",
      true,true,
      AdditionalTables::NONE,
      VolumeType::QUADRATIC
    },
    RenderMode{
      "Quadratic B-Spline + lighting + virtual Hermite Sampling, Prefiltered",
      "quadB.glsl",
      "virtHLighting.glsl",
      true,true,
      AdditionalTables::NONE,
      VolumeType::QUADRATIC
    },
    RenderMode{
      "Quadratic B-Spline + lighting + virtual monotone Hermite Sampling, Prefiltered",
      "quadB.glsl",
      "virtHmonotoneLighting.glsl",
      true,true,
      AdditionalTables::NONE,
      VolumeType::QUADRATIC
    },
    RenderMode{
      "Cubic B-Spline",
      "cubeB.glsl",
      "baseline.glsl",
      false,false,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Cubic B-Spline, Pre-Integrated",
      "cubeB.glsl",
      "baselinePre.glsl",
      false,false,
      AdditionalTables::PRE_INTEGRATION,
      VolumeType::RAW
    },
    RenderMode{
      "Cubic B-Spline Peak-Finding",
      "cubeB.glsl",
      "baselinePeak.glsl",
      false,false,
      AdditionalTables::PEAK_FINDING,
      VolumeType::RAW
    },
    RenderMode{
      "Cubic B-Spline + virtual linear Samples",
      "cubeB.glsl",
      "virtLinear.glsl",
      false,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Cubic B-Spline + virtual CR Samples",
      "cubeB.glsl",
      "virtCR.glsl",
      false,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Cubic B-Spline + virtual Hermite Samples",
      "cubeB.glsl",
      "virtH.glsl",
      false,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Cubic B-Spline + virtual monotone Hermite Samples",
      "cubeB.glsl",
      "virtHmonotone.glsl",
      false,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Cubic B-Spline + lighting",
      "cubeB.glsl",
      "lighting.glsl",
      true,false,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Cubic B-Spline Peak-Finding + lighting",
      "cubeB.glsl",
      "lightingPeak.glsl",
      true,false,
      AdditionalTables::PEAK_FINDING,
      VolumeType::RAW
    },
    RenderMode{
      "Cubic B-Spline + lighting + virtual linear Samples",
      "cubeB.glsl",
      "virtLinearLighting.glsl",
      true,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Cubic B-Spline + lighting + virtual CR Samples",
      "cubeB.glsl",
      "virtCRLighting.glsl",
      true,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Cubic B-Spline + lighting + virtual Hermite Samples",
      "cubeB.glsl",
      "virtHLighting.glsl",
      true,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Cubic B-Spline + lighting + virtual monotone Hermite Samples",
      "cubeB.glsl",
      "virtHmonotoneLighting.glsl",
      true,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },






    RenderMode{
      "Cubic B-Spline, Prefiltered",
      "cubeB.glsl",
      "baseline.glsl",
      false,false,
      AdditionalTables::NONE,
      VolumeType::CUBIC
    },
    RenderMode{
      "Cubic B-Spline, Pre-Integrated, Prefiltered",
      "cubeB.glsl",
      "baselinePre.glsl",
      false,false,
      AdditionalTables::PRE_INTEGRATION,
      VolumeType::CUBIC
    },
    RenderMode{
      "Cubic B-Spline Peak-Finding, Prefiltered",
      "cubeB.glsl",
      "baselinePeak.glsl",
      false,false,
      AdditionalTables::PEAK_FINDING,
      VolumeType::CUBIC
    },
    RenderMode{
      "Cubic B-Spline + virtual linear Samples, Prefiltered",
      "cubeB.glsl",
      "virtLinear.glsl",
      false,true,
      AdditionalTables::NONE,
      VolumeType::CUBIC
    },
    RenderMode{
      "Cubic B-Spline + virtual CR Samples, Prefiltered",
      "cubeB.glsl",
      "virtCR.glsl",
      false,true,
      AdditionalTables::NONE,
      VolumeType::CUBIC
    },
    RenderMode{
      "Cubic B-Spline + virtual Hermite Samples, Prefiltered",
      "cubeB.glsl",
      "virtH.glsl",
      false,true,
      AdditionalTables::NONE,
      VolumeType::CUBIC
    },
    RenderMode{
      "Cubic B-Spline + virtual monotone Hermite Samples, Prefiltered",
      "cubeB.glsl",
      "virtHmonotone.glsl",
      false,true,
      AdditionalTables::NONE,
      VolumeType::CUBIC
    },
    RenderMode{
      "Cubic B-Spline + lighting, Prefiltered",
      "cubeB.glsl",
      "lighting.glsl",
      true,false,
      AdditionalTables::NONE,
      VolumeType::CUBIC
    },
    RenderMode{
      "Cubic B-Spline Peak-Finding + lighting, Prefiltered",
      "cubeB.glsl",
      "lightingPeak.glsl",
      true,false,
      AdditionalTables::PEAK_FINDING,
      VolumeType::CUBIC
    },
    RenderMode{
      "Cubic B-Spline + lighting + virtual linear Samples, Prefiltered",
      "cubeB.glsl",
      "virtLinearLighting.glsl",
      true,true,
      AdditionalTables::NONE,
      VolumeType::CUBIC
    },
    RenderMode{
      "Cubic B-Spline + lighting + virtual CR Samples, Prefiltered",
      "cubeB.glsl",
      "virtCRLighting.glsl",
      true,true,
      AdditionalTables::NONE,
      VolumeType::CUBIC
    },
    RenderMode{
      "Cubic B-Spline + lighting + virtual Hermite Samples, Prefiltered",
      "cubeB.glsl",
      "virtHLighting.glsl",
      true,true,
      AdditionalTables::NONE,
      VolumeType::CUBIC
    },
    RenderMode{
      "Cubic B-Spline + lighting + virtual monotone Hermite Samples, Prefiltered",
      "cubeB.glsl",
      "virtHmonotoneLighting.glsl",
      true,true,
      AdditionalTables::NONE,
      VolumeType::CUBIC
    },
    RenderMode{
      "Catmull-Rom",
      "cubeCR.glsl",
      "baseline.glsl",
      false,false,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Catmull-Rom, Pre-Integrated",
      "cubeCR.glsl",
      "baselinePre.glsl",
      false,false,
      AdditionalTables::PRE_INTEGRATION,
      VolumeType::RAW
    },
    RenderMode{
      "Catmull-Rom Peak-Finding",
      "cubeCR.glsl",
      "baselinePeak.glsl",
      false,false,
      AdditionalTables::PEAK_FINDING,
      VolumeType::RAW
    },
    RenderMode{
      "Catmull-Rom + virtual linear Samples",
      "cubeCR.glsl",
      "virtLinear.glsl",
      false,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Catmull-Rom + virtual CR Samples",
      "cubeCR.glsl",
      "virtCR.glsl",
      false,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Catmull-Rom + virtual Hermite Samples",
      "cubeCR.glsl",
      "virtH.glsl",
      false,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Catmull-Rom + virtual monotone Hermite Samples",
      "cubeCR.glsl",
      "virtHmonotone.glsl",
      false,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Catmull-Rom + lighting",
      "cubeCR.glsl",
      "lighting.glsl",
      true,false,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Catmull-Rom Peak-Finding + lighting",
      "cubeCR.glsl",
      "lightingPeak.glsl",
      true,false,
      AdditionalTables::PEAK_FINDING,
      VolumeType::RAW
    },
    RenderMode{
      "Catmull-Rom + lighting + virtual linear Samples",
      "cubeCR.glsl",
      "virtLinearLighting.glsl",
      true,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Catmull-Rom + lighting + virtual CR Samples",
      "cubeCR.glsl",
      "virtCRLighting.glsl",
      true,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Catmull-Rom + lighting + virtual Hermite Samples",
      "cubeCR.glsl",
      "virtHLighting.glsl",
      true,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Catmull-Rom + lighting + virtual monotone Hermite Samples",
      "cubeCR.glsl",
      "virtHmonotoneLighting.glsl",
      true,true,
      AdditionalTables::NONE,
      VolumeType::RAW
    },
    RenderMode{
      "Analytic",
      "analytic.glsl",
      "baseline.glsl",
      false,false,
      AdditionalTables::NONE,
      VolumeType::NONE
    },
    RenderMode{
      "Analytic + lighting",
      "analytic.glsl",
      "lighting.glsl",
      true,false,
      AdditionalTables::NONE,
      VolumeType::NONE
    },
  };
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
