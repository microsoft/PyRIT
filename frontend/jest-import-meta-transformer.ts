/**
 * ts-jest AST transformer that replaces `import.meta.env.X` with
 * `process.env.X` so that ts-jest (CommonJS mode) can parse files
 * using Vite's `import.meta.env` convention.
 */
import ts from 'typescript'

const name = 'import-meta-env-transformer'
const version = '1'

function factory() {
  function visitNodeAndChildren(node: ts.Node, context: ts.TransformationContext): ts.Node {
    return ts.visitEachChild(
      visitSingleNode(node, context),
      (child) => visitNodeAndChildren(child, context),
      context
    )
  }

  function visitSingleNode(node: ts.Node, context: ts.TransformationContext): ts.Node {
    const f = context.factory

    // Match: import.meta.env.<KEY>
    if (
      ts.isPropertyAccessExpression(node) &&
      ts.isPropertyAccessExpression(node.expression) &&
      ts.isMetaProperty(node.expression.expression) &&
      node.expression.name.text === 'env'
    ) {
      return f.createPropertyAccessExpression(
        f.createPropertyAccessExpression(
          f.createIdentifier('process'),
          f.createIdentifier('env')
        ),
        node.name
      )
    }

    // Match: import.meta.env (without further property access)
    if (
      ts.isPropertyAccessExpression(node) &&
      ts.isMetaProperty(node.expression) &&
      node.name.text === 'env'
    ) {
      return f.createPropertyAccessExpression(
        f.createIdentifier('process'),
        f.createIdentifier('env')
      )
    }

    return node
  }

  return function transformer(context: ts.TransformationContext) {
    return function (sourceFile: ts.SourceFile): ts.SourceFile {
      return visitNodeAndChildren(sourceFile, context) as ts.SourceFile
    }
  }
}

export { name, version, factory }
