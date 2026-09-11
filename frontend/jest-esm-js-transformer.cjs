/**
 * Jest transformer for the ESM-only JavaScript dependencies that have to be
 * down-compiled to CommonJS (see `transformIgnorePatterns` in jest.config.ts).
 *
 * ts-jest only runs `astTransformers` over TypeScript sources, so those
 * packages keep their `import.meta` references and fail to parse once emitted
 * as CommonJS — react-router's `import.meta.hot` guard is one such case.
 * Neutralize `import.meta` in the source before delegating to ts-jest.
 */
const tsJest = require('ts-jest')

const createTsJestTransformer = (tsJest.default ?? tsJest).createTransformer

const neutralizeImportMeta = (source) =>
  source.replace(/\bimport\.meta\.env\b/g, 'process.env').replace(/\bimport\.meta\b/g, '({})')

module.exports = {
  createTransformer(config) {
    const transformer = createTsJestTransformer(config)

    for (const method of ['process', 'processAsync', 'getCacheKey', 'getCacheKeyAsync']) {
      const original = transformer[method]
      if (typeof original !== 'function') continue
      const bound = original.bind(transformer)
      transformer[method] = (source, path, options) =>
        bound(neutralizeImportMeta(source), path, options)
    }

    return transformer
  },
}
