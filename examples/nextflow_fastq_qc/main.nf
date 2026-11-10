nextflow.enable.dsl=2

params.input = null
params.outdir = './results'
params.min_length = 20

if (!params.input) error 'Missing --input samplesheet'

process FASTQ_QC {
    tag "$sample"
    publishDir params.outdir, mode: 'copy', overwrite: true
    input:
    tuple val(sample), path(reads)
    output:
    path("${sample}.qc.tsv")
    script:
    """
    awk 'BEGIN { n=0; gc=0; bases=0 } NR%4==2 { n++; bases+=length(\$0); for(i=1;i<=length(\$0);i++){c=substr(\$0,i,1); if(c=="G"||c=="C"||c=="g"||c=="c") gc++} } END { print "sample\\treads\\tgc_percent"; if (bases>0) printf "%s\\t%d\\t%.2f\\n", "${sample}",n,100*gc/bases; else printf "%s\\t0\\t0\\n", "${sample}" }' ${reads} > ${sample}.qc.tsv
    """
}

workflow {
    Channel.fromPath(params.input)
        .splitCsv(header: true)
        .map { row -> tuple(row.sample, file(row.fastq_1)) }
        .set { samples }
    FASTQ_QC(samples)
}
