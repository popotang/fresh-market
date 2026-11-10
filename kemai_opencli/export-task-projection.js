const PUBLIC_FIELDS=[
  'id','taskNo','fileName','sheetType','status','totalRows',
  'createTime','gmtCreate','gmtModified','failReason',
  'reuse_reason','reused_task_no','requested_file_name',
];

export function publicExportTask(task){
  const source=task&&typeof task==='object'?task:{};
  const projected={};
  for(const field of PUBLIC_FIELDS)if(source[field]!==undefined)projected[field]=source[field];
  projected.download_ready=String(source.status)==='2'&&source.totalRows!=null&&source.totalRows!==''&&Boolean(source.sourceUrl);
  return projected;
}
