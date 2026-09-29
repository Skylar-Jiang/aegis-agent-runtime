import type { TaskGraphSnapshot } from '../api/taskGraphs'

type GraphNode = TaskGraphSnapshot['nodes'][number]

export function layoutGraphNodes(nodes: GraphNode[]) {
  const byId = new Map(nodes.map((node) => [node.node_id, node]))
  const order = new Map(nodes.map((node, index) => [node.node_id, index]))
  const depths = new Map<string, number>()
  const depthFor = (node: GraphNode): number => {
    const known = node.dependencies
      .map((id) => byId.get(id))
      .filter((item): item is GraphNode => Boolean(item))
    if (!known.length) return 0
    if (depths.has(node.node_id)) return depths.get(node.node_id)!
    const depth = Math.max(...known.map(depthFor)) + 1
    depths.set(node.node_id, depth)
    return depth
  }
  const layers = new Map<number, GraphNode[]>()
  nodes.forEach((node) => {
    const depth = depthFor(node)
    layers.set(depth, [...(layers.get(depth) ?? []), node])
  })
  layers.forEach((layer) =>
    layer.sort((left, right) => {
      const leftParent = Math.min(
        ...left.dependencies.map(
          (dependency) => order.get(dependency) ?? order.get(left.node_id)!,
        ),
        order.get(left.node_id)!,
      )
      const rightParent = Math.min(
        ...right.dependencies.map(
          (dependency) => order.get(dependency) ?? order.get(right.node_id)!,
        ),
        order.get(right.node_id)!,
      )
      return leftParent - rightParent
    }),
  )
  const layerCount = Math.max(...layers.keys(), 0) + 1
  const rowCount = Math.max(
    ...[...layers.values()].map((layer) => layer.length),
    1,
  )
  const positions = new Map<string, { node: GraphNode; x: number; y: number }>()
  layers.forEach((layer, column) =>
    layer.forEach((node, row) =>
      positions.set(node.node_id, {
        node,
        x: 130 + column * 310,
        y: 76 + row * 142,
      }),
    ),
  )
  return {
    positions,
    width: Math.max(560, layerCount * 310 + 70),
    height: Math.max(250, rowCount * 142 + 86),
  }
}
