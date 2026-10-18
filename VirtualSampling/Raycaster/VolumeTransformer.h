#pragma once

#include "Volume.h"

namespace VolumeTransformer {

  Volume<float> prefilterCubicBSpline(const Volume<uint8_t>& inputVolume);
  Volume<float> prefilterQuadraticBSpline(const Volume<uint8_t>& inputVolume);

  bool testPrefilterCubic(const Volume<uint8_t>& inputVolume,
                          const Volume<float>& prefilteredVolume,
                          float maxError = 0.0019f,
                          size_t border = 2);

  bool testPrefilterQuad(const Volume<uint8_t>& inputVolume,
                         const Volume<float>& prefilteredVolume,
                         float maxError = 0.0019f,
                         size_t border = 2);

  template <typename T>
  Volume<T> downBy2(const Volume<T>& src) {
    auto ensureDivisibleByTwo = [](size_t value, const char* name) {
      if ((value % 2) != 0) {
        throw std::runtime_error(std::string("Dimension '") + name +
                                 "' must be 1 or divisible by 2.");
      }
    };

    auto castAverage = [](long double v) -> T {
      if constexpr (std::is_integral_v<T>) {
        return static_cast<T>(std::llround(v));
      } else {
        return static_cast<T>(v);
      }
    };

    const size_t srcW = src.metaData.width;
    const size_t srcH = src.metaData.height;
    const size_t srcD = src.metaData.depth;

    if (srcW == 0 || srcH == 0 || srcD == 0) {
      return Volume<T>{};
    }

    ensureDivisibleByTwo(srcW, "width");
    ensureDivisibleByTwo(srcH, "height");
    ensureDivisibleByTwo(srcD, "depth");

    const size_t dstW = srcW / 2;
    const size_t dstH = srcH / 2;
    const size_t dstD = srcD / 2;

    VolumeMetaData dstMeta;
    dstMeta.width = dstW;
    dstMeta.height = dstH;
    dstMeta.depth = dstD;
    dstMeta.scale = src.metaData.scale;

    Volume<T> dst(dstMeta);

    for (size_t z = 0; z < dstD; ++z) {
      const size_t srcZ0 = z * 2;
      for (size_t y = 0; y < dstH; ++y) {
        const size_t srcY0 = y * 2;
        for (size_t x = 0; x < dstW; ++x) {
          const size_t srcX0 = x * 2;

          long double sum = 0.0L;
          for (size_t dz = 0; dz < 2; ++dz) {
            for (size_t dy = 0; dy < 2; ++dy) {
              for (size_t dx = 0; dx < 2; ++dx) {
                sum += static_cast<long double>(
                  src.getValue(srcX0 + dx, srcY0 + dy, srcZ0 + dz)
                );
              }
            }
          }

          const long double avg = sum / 8.0;
          const size_t dstIndex = x + y * dstW + z * dstW * dstH;
          dst.data[dstIndex] = castAverage(avg);
        }
      }
    }

    return dst;
  }

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
