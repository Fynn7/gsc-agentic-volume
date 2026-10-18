#pragma once

#include "Volume.h"

namespace VolumeGenerator {
  Volume<uint8_t> marschnerLobb(size_t width = 256,
                                size_t height = 256,
                                size_t depth = 256,
                                float alpha = 0.25f,
                                float fM = 6.0f);

  Volume<uint8_t> linear(size_t width = 256,
                         size_t height = 256,
                         size_t depth = 256);

  Volume<uint8_t> ramp(size_t width = 256,
                       size_t height = 256,
                       size_t depth = 256);

  Volume<uint8_t> sphere(size_t width = 256,
                         size_t height = 256,
                         size_t depth = 256);
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
