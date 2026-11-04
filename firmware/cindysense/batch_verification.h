#pragma once

#include <ctype.h>
#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

struct SensorSample {
  uint32_t timestamp;
  float ax;
  float ay;
  float az;
  float gx;
  float gy;
  float gz;
};

enum VerificationResult : int {
  VERIFY_MATCH,
  VERIFY_NOT_FOUND,
  VERIFY_UNKNOWN,
  VERIFY_MISMATCH
};

namespace cindysense_json {

struct Span {
  const char *begin;
  const char *end;
};

inline const char *skipSpace(const char *p, const char *end) {
  while (p < end && isspace((unsigned char)*p)) ++p;
  return p;
}

inline bool parseString(const char *p, const char *end, Span &value,
                        const char *&after) {
  p = skipSpace(p, end);
  if (p >= end || *p != '"') return false;
  const char *start = p++;
  while (p < end) {
    unsigned char ch = (unsigned char)*p++;
    if (ch == '"') {
      value = {start, p};
      after = p;
      return true;
    }
    if (ch < 0x20) return false;
    if (ch == '\\') {
      if (p >= end) return false;
      char escaped = *p++;
      if (escaped == 'u') {
        for (int i = 0; i < 4; ++i) {
          if (p >= end || !isxdigit((unsigned char)*p++)) return false;
        }
      } else if (!strchr("\\\"/bfnrt", escaped)) {
        return false;
      }
    }
  }
  return false;
}

inline bool skipValue(const char *p, const char *end, Span &value,
                      const char *&after, uint8_t depth = 0) {
  p = skipSpace(p, end);
  if (p >= end || depth > 16) return false;
  const char *start = p;
  if (*p == '"') {
    Span ignored;
    if (!parseString(p, end, ignored, after)) return false;
  } else if (*p == '{' || *p == '[') {
    const char close = (*p == '{') ? '}' : ']';
    const char open = *p++;
    p = skipSpace(p, end);
    if (p < end && *p == close) {
      after = p + 1;
    } else {
      while (p < end) {
        if (open == '{') {
          Span key;
          if (!parseString(p, end, key, p)) return false;
          p = skipSpace(p, end);
          if (p >= end || *p++ != ':') return false;
        }
        Span child;
        if (!skipValue(p, end, child, p, depth + 1)) return false;
        p = skipSpace(p, end);
        if (p < end && *p == close) {
          after = p + 1;
          break;
        }
        if (p >= end || *p++ != ',') return false;
        p = skipSpace(p, end);
      }
      if (p >= end && (end == start || end[-1] != close)) return false;
    }
  } else {
    while (p < end && *p != ',' && *p != ']' && *p != '}' &&
           !isspace((unsigned char)*p)) ++p;
    if (p == start) return false;
    after = p;
  }
  value = {start, after};
  return true;
}

inline bool stringEquals(const Span &span, const char *expected) {
  if (span.begin >= span.end || *span.begin != '"') return false;
  size_t index = 0;
  for (const char *p = span.begin + 1; p < span.end - 1; ++p) {
    char ch = *p;
    if (ch == '\\') {
      if (++p >= span.end - 1) return false;
      ch = *p;
      if (ch == 'u' || strchr("bfnrt", ch)) return false;
    }
    if (!expected[index] || expected[index++] != ch) return false;
  }
  return expected[index] == '\0';
}

inline bool decodeString(const Span &span, char *out, size_t capacity) {
  if (!capacity || span.begin >= span.end || *span.begin != '"') return false;
  size_t used = 0;
  for (const char *p = span.begin + 1; p < span.end - 1; ++p) {
    char ch = *p;
    if (ch == '\\') {
      if (++p >= span.end - 1) return false;
      ch = *p;
      if (ch == 'u') return false;
      switch (ch) {
        case 'b': ch = '\b'; break;
        case 'f': ch = '\f'; break;
        case 'n': ch = '\n'; break;
        case 'r': ch = '\r'; break;
        case 't': ch = '\t'; break;
        default: break;
      }
    }
    if (used + 1 >= capacity) return false;
    out[used++] = ch;
  }
  out[used] = '\0';
  return true;
}

inline bool member(const Span &object, const char *name, Span &found) {
  const char *p = skipSpace(object.begin, object.end);
  if (p >= object.end || *p++ != '{') return false;
  p = skipSpace(p, object.end);
  if (p < object.end && *p == '}') return false;
  while (p < object.end) {
    Span key;
    if (!parseString(p, object.end, key, p)) return false;
    p = skipSpace(p, object.end);
    if (p >= object.end || *p++ != ':') return false;
    Span value;
    if (!skipValue(p, object.end, value, p)) return false;
    if (stringEquals(key, name)) {
      found = value;
      return true;
    }
    p = skipSpace(p, object.end);
    if (p < object.end && *p == '}') return false;
    if (p >= object.end || *p++ != ',') return false;
    p = skipSpace(p, object.end);
  }
  return false;
}

inline bool number(const Span &span, double &out) {
  if (span.begin >= span.end || span.end - span.begin >= 48) return false;
  char text[48];
  size_t length = span.end - span.begin;
  memcpy(text, span.begin, length);
  text[length] = '\0';
  char *tail = nullptr;
  errno = 0;
  out = strtod(text, &tail);
  return errno == 0 && tail == text + length && isfinite(out);
}

inline bool arrayElement(const Span &array, size_t wanted, Span &found,
                         size_t &count) {
  const char *p = skipSpace(array.begin, array.end);
  if (p >= array.end || *p++ != '[') return false;
  p = skipSpace(p, array.end);
  count = 0;
  if (p < array.end && *p == ']') return wanted == 0;
  while (p < array.end) {
    Span element;
    if (!skipValue(p, array.end, element, p)) return false;
    if (count == wanted) found = element;
    ++count;
    p = skipSpace(p, array.end);
    if (p < array.end && *p == ']') return true;
    if (p >= array.end || *p++ != ',') return false;
  }
  return false;
}

inline bool arrayLength(const Span &array, size_t &count) {
  const char *p = skipSpace(array.begin, array.end);
  if (p >= array.end || *p++ != '[') return false;
  p = skipSpace(p, array.end);
  count = 0;
  if (p < array.end && *p == ']') return true;
  while (p < array.end) {
    Span ignored;
    if (!skipValue(p, array.end, ignored, p)) return false;
    ++count;
    p = skipSpace(p, array.end);
    if (p < array.end && *p == ']') return true;
    if (p >= array.end || *p++ != ',') return false;
  }
  return false;
}

inline bool nearlyEqual(double actual, double expected) {
  return isfinite(actual) && fabs(actual - expected) <= 0.00051;
}

inline bool sampleMatches(const Span &json, const SensorSample &sample) {
  const char *fields[] = {"ax", "ay", "az", "gx", "gy", "gz"};
  const float expected[] = {sample.ax, sample.ay, sample.az,
                            sample.gx, sample.gy, sample.gz};
  Span value;
  double actual;
  if (!member(json, "t", value) || !number(value, actual) ||
      actual != (double)sample.timestamp) return false;
  for (size_t i = 0; i < 6; ++i) {
    if (!member(json, fields[i], value) || !number(value, actual) ||
        !nearlyEqual(actual, expected[i])) return false;
  }
  return true;
}

inline bool contentMatches(const Span &content, const char *batchId,
                           const SensorSample *samples, size_t sampleCount) {
  Span batch, array;
  if (!member(content, "batchId", batch) || !stringEquals(batch, batchId) ||
      !member(content, "samples", array)) return false;
  size_t actualCount = 0;
  if (!arrayLength(array, actualCount) || actualCount != sampleCount) return false;
  for (size_t i = 0; i < sampleCount; ++i) {
    Span item;
    size_t count = 0;
    if (!arrayElement(array, i, item, count) ||
        !sampleMatches(item, samples[i])) return false;
  }
  return true;
}

}  // namespace cindysense_json

inline VerificationResult verifyBatchHttpResponse(
    int httpCode, const char *response, size_t responseLength,
    const char *batchId, const SensorSample *samples, size_t sampleCount,
    char *decodedCon, size_t decodedCapacity) {
  if (httpCode == 404) return VERIFY_NOT_FOUND;
  if (httpCode != 200 || !response || !batchId || !samples || !sampleCount)
    return VERIFY_UNKNOWN;

  using namespace cindysense_json;
  Span root = {response, response + responseLength};
  Span cin, con, resourceName;
  if (!member(root, "m2m:cin", cin) ||
      !member(cin, "con", con)) return VERIFY_MISMATCH;
  if (member(cin, "rn", resourceName) && !stringEquals(resourceName, batchId))
    return VERIFY_MISMATCH;

  if (con.begin < con.end && *con.begin == '"') {
    if (!decodedCon || !decodeString(con, decodedCon, decodedCapacity))
      return VERIFY_MISMATCH;
    con = {decodedCon, decodedCon + strlen(decodedCon)};
  }
  return contentMatches(con, batchId, samples, sampleCount)
             ? VERIFY_MATCH : VERIFY_MISMATCH;
}
